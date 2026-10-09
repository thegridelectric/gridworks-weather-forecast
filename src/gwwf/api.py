"""HTTP / FastAPI read façade — gwwf's public pull path.

**Broadcasts are the delivery; HTTP is the single pull path** — drop
recovery, fallback when a relay goes quiet, post-hoc reads, and the
record listing. Public, read-only, CORS-open, TLS at the fronting
proxy. House pattern: routes ride `/<party>/…` where the party for a
GNode service is its **hyphenated GNodeAlias** (the LRH transform the
routing keys use — gwwf is a GNode, unlike gnr whose party segment is
the bare service name), derived from settings at app build. Every
response body is a **sema word**: routes return the sema types
themselves with `response_model_exclude_none`, byte-identical to
`to_dict()` wire form (pinned DB-free by `tests/test_api_wire.py`),
so `/docs` documents the real sema schemas. Point lookups are GETs
with format-typed path params — the sanctioned exception to
sema-word bodies. Thin by design: HTTP over the `WeatherReads` seam;
the sole-accessor store is the default source, harnesses inject
fakes.

Run: `uv run uvicorn gwwf.api:app --factory` (env-configured).
"""

from __future__ import annotations

from typing import Any, Protocol

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from gwwf.config import GwwfSettings
from gwwf.db.session import session_factory_from
from gwwf.db.store import (
    latest_forecast,
    load_bundles,
    load_forecast_channels,
    load_last_observation,
    load_locations,
    load_seasonal_templates,
    load_weather_channels,
)
from gwwf.sema.property_format import LeftRightDot
from gwwf.sema.types import (
    WeatherChannelGt,
    WeatherForecast,
    WeatherForecastBundleGt,
    WeatherForecastChannelGt,
    WeatherLocationGt,
    WeatherObservation,
    WeatherSeasonalTemplateGt,
)

# Where a sema word's definition lives — one constant so the whole docs
# surface flips together when schemas.electricity.works stands up.
SEMA_DEFINITION_URL = (
    "https://github.com/thegridelectric/sema/blob/dev/"
    "definitions/types/{type_name}/{version}.yaml"
)


class WeatherReads(Protocol):
    """The read seam the façade serves — store-backed by default."""

    def channels(self) -> list[WeatherChannelGt]: ...
    def forecast_channels(self) -> list[WeatherForecastChannelGt]: ...
    def bundles(self) -> list[WeatherForecastBundleGt]: ...
    def locations(self) -> list[WeatherLocationGt]: ...
    def seasonal_templates(self) -> list[WeatherSeasonalTemplateGt]: ...
    def latest_observation(self, location_alias: str) -> WeatherObservation | None: ...
    def latest_forecast(self, bundle_name: str) -> WeatherForecast | None: ...


class StoreReads:
    """`WeatherReads` over the sole-accessor store."""

    def __init__(self, settings: GwwfSettings) -> None:
        self._sessions = session_factory_from(settings)

    def channels(self) -> list[WeatherChannelGt]:
        with self._sessions() as session:
            return load_weather_channels(session)

    def forecast_channels(self) -> list[WeatherForecastChannelGt]:
        with self._sessions() as session:
            return load_forecast_channels(session)

    def bundles(self) -> list[WeatherForecastBundleGt]:
        with self._sessions() as session:
            return load_bundles(session)

    def locations(self) -> list[WeatherLocationGt]:
        with self._sessions() as session:
            return load_locations(session)

    def seasonal_templates(self) -> list[WeatherSeasonalTemplateGt]:
        with self._sessions() as session:
            return load_seasonal_templates(session)

    def latest_observation(self, location_alias: str) -> WeatherObservation | None:
        with self._sessions() as session:
            stored = load_last_observation(session, location_alias)
        return stored.observation if stored else None

    def latest_forecast(self, bundle_name: str) -> WeatherForecast | None:
        with self._sessions() as session:
            return latest_forecast(session, bundle_name)


def create_app(
    settings: GwwfSettings | None = None, *, source: WeatherReads | None = None
) -> FastAPI:
    """Build the read façade; the party segment is the hyphenated alias."""
    settings = settings or GwwfSettings()
    reads: WeatherReads = source if source is not None else StoreReads(settings)
    party = settings.service_alias.replace(".", "-")  # LRH, as in routing keys

    app = FastAPI(
        title="gridworks-weather-forecast — read API",
        description=(
            "Public read-only pull path for GridWorks weather. Every "
            "response body is a sema word; message words are byte-identical "
            "to their broadcasts."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    router = APIRouter(prefix=f"/{party}")

    @router.get("/channels", response_model_exclude_none=True)
    def channels() -> list[WeatherChannelGt]:
        """The observed-series channel records."""
        return reads.channels()

    @router.get("/forecast-channels", response_model_exclude_none=True)
    def forecast_channels() -> list[WeatherForecastChannelGt]:
        """The forecast channel records (predictor + shape + slice grid)."""
        return reads.forecast_channels()

    @router.get("/bundles", response_model_exclude_none=True)
    def bundles() -> list[WeatherForecastBundleGt]:
        """The forecast bundle records — the sign-up objects."""
        return reads.bundles()

    @router.get("/seasonal-templates", response_model_exclude_none=True)
    def seasonal_templates() -> list[WeatherSeasonalTemplateGt]:
        """The seasonal template records, every location's and every
        Start; a consumer takes the latest Start for its location."""
        return reads.seasonal_templates()

    @router.get("/locations", response_model_exclude_none=True)
    def locations() -> list[WeatherLocationGt]:
        """The place-anchor records."""
        return reads.locations()

    @router.get(
        "/latest-observation/{location_alias}", response_model_exclude_none=True
    )
    def latest_observation(location_alias: LeftRightDot) -> WeatherObservation:
        """The last real observation for a location (404 if none stored)."""
        message = reads.latest_observation(location_alias)
        if message is None:
            raise HTTPException(
                status_code=404, detail=f"no observation for {location_alias}"
            )
        return message

    @router.get("/latest-forecast/{bundle_name}", response_model_exclude_none=True)
    def latest_forecast(bundle_name: LeftRightDot) -> WeatherForecast:
        """The newest sent forecast for a bundle (404 if none stored)."""
        message = reads.latest_forecast(bundle_name)
        if message is None:
            raise HTTPException(
                status_code=404, detail=f"no forecast for {bundle_name}"
            )
        return message

    app.include_router(router)

    def openapi_with_sema_links() -> dict[str, Any]:
        """Every schema that is a sema word (TypeName/Version defaults
        present) links to its canonical definition — derived from the
        schema itself, never hand-maintained."""
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        for component in schema.get("components", {}).get("schemas", {}).values():
            properties = component.get("properties", {})
            type_name = properties.get("TypeName", {}).get("default")
            version = properties.get("Version", {}).get("default")
            if type_name and version:
                url = SEMA_DEFINITION_URL.format(type_name=type_name, version=version)
                note = f"Sema word [`{type_name}` v{version}]({url})."
                component["description"] = (
                    f"{component.get('description', '')}\n\n{note}".strip()
                )
        app.openapi_schema = schema
        return schema

    app.openapi = openapi_with_sema_links  # type: ignore[method-assign]
    return app


def app() -> FastAPI:
    """uvicorn factory: `uvicorn gwwf.api:app --factory` (env settings)."""
    import dotenv

    dotenv.load_dotenv(dotenv.find_dotenv())
    return create_app()
