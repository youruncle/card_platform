import type { CardRecognition } from "./contracts";
export type RecognitionInput = Partial<
  Omit<CardRecognition, "confidence" | "web_candidates">
> & {
  confidence: string | number;
  web_candidates?: Array<
    Pick<CardRecognition["web_candidates"][number], "source_urls" | "conflicts">
  >;
};
