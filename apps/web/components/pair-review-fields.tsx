"use client";

import { pairReviewState, type PairReview, type RecognitionInput } from "../../../packages/card-core/src/recognition";

export function PairReviewFields({ result, paired, review, onChange, disabled = false }: {
  result: Pick<RecognitionInput, "pair_validation">;
  paired: boolean;
  review: PairReview | null;
  onChange: (review: PairReview | null) => void;
  disabled?: boolean;
}) {
  const state = pairReviewState(result, paired, review);
  if (!paired) return null;
  return <section aria-label="正反面核对">
    {state.status === "conflict" && <p role="alert">正反面存在明确冲突，请重新拍摄或选择照片后识别。</p>}
    {state.reasons.map(reason => <p key={reason}>{reason}</p>)}
    {state.status === "unknown" && (state.imageHashes ? <label className="check">
      <input type="checkbox" checked={state.reviewed} disabled={disabled} onChange={event => onChange(event.target.checked ? {confirmed: true, image_content_hashes: state.imageHashes!} : null)} />
      我已查看当前正反面，确认属于同一张卡
    </label> : <p>请重新识别当前两面照片，再核对配对关系。</p>)}
  </section>;
}
