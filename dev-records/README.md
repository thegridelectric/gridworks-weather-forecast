# dev-records

The record set a dev weather service is minted with: one location,
two stations, three observed channels, three forecast channels, the
bobcat and raccoon bundles and the seasonal template, numbered in the
referential order `gwwf create` needs. `scripts/dev-up.sh` mints them
over the local dev broker onto an empty database.

Ids are fixed, so every dev up re-mints the same identities and each
bundle file embeds its channels byte-equal to the channel files. The
same files mint the production record set by hand.
