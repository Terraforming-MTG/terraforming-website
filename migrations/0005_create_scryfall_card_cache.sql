CREATE TABLE scryfall_card_cache (
    oracle_id TEXT PRIMARY KEY NOT NULL,
    name TEXT,
    image_uri TEXT,
    scryfall_uri TEXT,
    cached_at DATETIME NOT NULL
);
