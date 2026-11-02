CREATE TABLE cards (
    oracle_id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    type_line TEXT,
    image_uri TEXT,
    scryfall_uri TEXT
);

CREATE TABLE format_lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE
);

CREATE TABLE format_list_cards (
    list_id INTEGER NOT NULL REFERENCES format_lists(id),
    oracle_id TEXT NOT NULL REFERENCES cards(oracle_id),
    reason TEXT,
    added_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (list_id, oracle_id)
);

INSERT INTO format_lists (name, slug)
VALUES ('Banlist', 'banlist'), ('Reserved', 'reserved');
