export type ComparableWithId = { id: string };

export function selectUsedComparableRows<T extends ComparableWithId>(
  rows: readonly T[],
  usedIds: readonly string[],
): T[] {
  const rowsById = new Map(rows.map((row) => [row.id, row]));
  return usedIds
    .map((id) => rowsById.get(id))
    .filter((row): row is T => row !== undefined);
}

export function acceptedEvidenceComparableIds(
  embeddedIds: readonly string[],
  usedIds: readonly string[],
  shadowUsedIds: readonly string[],
): Set<string> {
  return new Set([...embeddedIds, ...usedIds, ...shadowUsedIds]);
}
