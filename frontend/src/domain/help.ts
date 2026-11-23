// Textos de ayuda que explican los datos en la interfaz.

/** Cómo se estima la biomasa quemada. */
export const BIOMASS_HELP =
  "Burned biomass (estimate): radiated energy of the fire (the FRP of each satellite pass " +
  "× the time it represents, up to 6 h) × 0.368 kg/MJ. Useful to compare fires; it " +
  "does not include what satellites do not detect.";

/** Cómo se mide la duración. */
export const DURATION_HELP =
  "Duration: time between the first and the last satellite detection. It is a minimum: the fire " +
  "starts before the first pass and continues after the last one.";

/** Qué es una detección. */
export const DETECTION_HELP =
  "A detection is a pixel in which a satellite (MODIS or VIIRS) recorded active fire during " +
  "a pass. A large or long fire adds up many detections.";

/** La huella no es el área quemada (nota al pie, marcada con *). */
export const FOOTPRINT_NOTE = "*The footprint (area of the detected pixels) is not burned area.";
