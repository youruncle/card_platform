import assert from "node:assert/strict";
import test from "node:test";

import {
  acceptedEvidenceComparableIds,
  selectUsedComparableRows,
} from "./valuation-evidence.ts";

test("T0 embedded evidence remains accepted", () => {
  const accepted = acceptedEvidenceComparableIds(["t0-a", "t0-b"], [], []);

  assert.deepEqual([...accepted], ["t0-a", "t0-b"]);
});

test("T2 used evidence links only the explicitly selected market rows", () => {
  const rows = [
    { id: "unrelated", price: "999" },
    { id: "used-b", price: "18" },
    { id: "used-a", price: "1" },
  ];

  assert.deepEqual(
    selectUsedComparableRows(rows, ["used-a", "used-b"]),
    [rows[2], rows[1]],
  );
});

test("a missing used ID never falls back to all market candidates", () => {
  const rows = [{ id: "rejected-a" }, { id: "rejected-b" }];

  assert.deepEqual(selectUsedComparableRows(rows, ["missing-used-id"]), []);
  assert.equal(
    acceptedEvidenceComparableIds([], ["missing-used-id"], []).has(
      "missing-used-id",
    ),
    true,
  );
});
