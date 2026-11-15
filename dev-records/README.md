# dev-records

The record set a dev weather service is minted with: one location,
three stations, six observed channels, five forecast channels, the
bobcat, raccoon and moose bundles and the seasonal template, numbered in
the referential order `gwwf create` needs. The `ommillinocket` station
is the Open-Meteo model gridpoint at Millinocket: its three channels
are filled by `gwwf archive` and have no live observation stream. Moose is the 96-hour
temperature-and-wind bundle a scada names: its two NWS channels forecast
the same `kmlt` observations as raccoon's over a longer grid. `scripts/dev-up.sh` mints them
over the local dev broker onto an empty database.

Ids are fixed, so every dev up re-mints the same identities and each
bundle file embeds its channels byte-equal to the channel files. The
same files mint the production record set by hand.
