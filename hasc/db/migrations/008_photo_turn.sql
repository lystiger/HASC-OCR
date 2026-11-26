-- Extra turn for a photo that is upside down or sideways after the portrait-to-landscape step, in degrees
-- counter-clockwise (0, 90, 180, 270). The worker asks the reading model before each read; the screen uses it too.
ALTER TABLE photos ADD COLUMN turn INTEGER NOT NULL DEFAULT 0;
