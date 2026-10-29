import type { CardRecognition } from "./contracts";
export type RecognitionInput = Partial<
  Omit<CardRecognition, "confidence" | "web_candidates">
> & {
  confidence: string | number;
  web_candidates?: Array<
    Pick<CardRecognition["web_candidates"][number], "source_urls" | "conflicts">
  >;
};

export type PairReview = {
  confirmed: true;
  image_content_hashes: { front: string; back: string };
};

const pairReasonLabels: Record<string, string> = {
  player_conflict: "两面显示的球员身份不同",
  subject_conflict: "两面显示的主体身份不同",
  brand_conflict: "两面显示的品牌不一致",
  product_conflict: "两面显示的产品不同",
  insert_conflict: "两面显示的特卡系列不同",
  card_number_conflict: "两面显示的卡号不同",
  duplicate_front: "两张照片都显示正面，请补拍真实背面",
  duplicate_back: "两张照片都显示背面，请补拍真实正面",
  duplicate_image: "两张照片重复，请补拍另一面",
  identical_images: "两张照片重复，请补拍另一面",
  identity_unverified: "尚未确认两面属于同一张卡",
  independent_face_evidence_missing: "缺少逐面身份依据，请核对两面照片",
  pair_not_verified: "尚未确认两面属于同一张卡",
  reported_face_conflict: "识别结果发现正反面身份冲突",
};

/** A review is valid only for the exact images the server evaluated. */
export function pairReviewState(
  result: Pick<RecognitionInput, "pair_validation">,
  paired: boolean,
  review: PairReview | null = null,
) {
  const validation = result.pair_validation;
  const hashes = validation?.image_content_hashes;
  const imageHashes = hashes?.front && hashes?.back
    ? { front: hashes.front, back: hashes.back }
    : null;
  const status = !paired ? "not_required"
    : validation?.status === "conflict" ? "conflict"
    : validation?.status === "consistent" && imageHashes ? "consistent"
    : "unknown";
  const reviewed = !!imageHashes && review?.confirmed === true
    && review.image_content_hashes.front === imageHashes.front
    && review.image_content_hashes.back === imageHashes.back;
  const reasons = [...new Set([
    ...(validation?.evidence ?? []),
    ...(validation?.reason_codes ?? []).map(code => pairReasonLabels[code]).filter(Boolean),
  ])];
  return { status, imageHashes, reviewed, reasons,
    blocked: status === "conflict" };
}

export function pairReviewPayload(
  result: Pick<RecognitionInput, "pair_validation">,
  paired: boolean,
  review: PairReview | null,
): { pair_review?: PairReview } {
  const state = pairReviewState(result, paired, review);
  if (state.status === "conflict")
    throw new Error("正反面存在明确冲突，请更换照片后重新识别");
  return state.status === "unknown" && state.reviewed ? {pair_review: review!} : {};
}
