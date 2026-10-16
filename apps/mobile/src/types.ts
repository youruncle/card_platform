import type {
  AuthUser,
  PortfolioItem,
  PortfolioPage,
  AutomatedValuationDetail,
  CaptureJob,
} from "../../../packages/card-core/src/contracts";
export type User = AuthUser;
export interface Session {
  user: User;
  token: string;
  expires_at: string;
  origin: string;
}
export type Card = Pick<
  PortfolioItem,
  | "id"
  | "player_name"
  | "brand_name"
  | "product_name"
  | "season_start"
  | "remaining_cost"
> &
  Partial<PortfolioItem>;
export type Page = Pick<
  PortfolioPage,
  "items" | "total" | "page" | "total_pages" | "snapshot_token" | "snapshot_at"
>;
export type { RecognitionInput as Recognition } from "../../../packages/card-core/src/recognition";
import type { RecognitionInput as Recognition } from "../../../packages/card-core/src/recognition";
export type Job = Pick<
  CaptureJob,
  | "id"
  | "status"
  | "collection_item_id"
  | "error_message"
  | "attempt_count"
  | "created_at"
> &
  Pick<CaptureJob, "front_image_url" | "back_image_url"> &
  Partial<
    Pick<
      CaptureJob,
      "target_item_id" | "available_sides" | "revision" | "images"
    >
  > & { result: Recognition | null };
export type Evidence = Pick<
  AutomatedValuationDetail,
  "status" | "error_message" | "comparables"
>;
