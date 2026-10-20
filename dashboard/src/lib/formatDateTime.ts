/**
 * Render an ISO instant (API times carry Z/offset) in the viewer's zone, or
 * `timeZone` if given, with a zone label. Unparseable input is returned as-is.
 */
export function formatDateTime(
  iso: string,
  locale: string,
  timeZone?: string,
): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(locale, {
    // dateStyle/timeStyle cannot be combined with timeZoneName (RangeError),
    // so spell out the equivalent medium date + short time fields.
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone,
    timeZoneName: "short",
  }).format(d);
}
