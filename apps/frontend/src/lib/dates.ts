"use client";

/**
 * Format one ISO-like timestamp into a deterministic UTC label.
 */
export function formatUtcDateTime(value: string | null): string {
  if (!value) {
    return "-";
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  const iso = parsed.toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 19)} UTC`;
}
