-- Store the layout the reader found on the sheet's photo: 'form_a' or 'back'. NULL for sheets read before this migration.

ALTER TABLE sheets ADD COLUMN layout TEXT;
