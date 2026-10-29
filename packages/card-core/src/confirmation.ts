import type { RecognitionInput as Recognition } from "./recognition";
export function confirmationPayload(
  owner: string,
  result: Recognition,
  fields: Record<string, string>,
) {
  result = { ...result, ...Object.fromEntries(
    Object.entries(result.field_assessments ?? {})
      .filter(([, assessment]) => ["unknown", "conflicted"].includes(assessment.status))
      .map(([field]) => [field, null]),
  ) };
  const nullable = (key: string, fallback: unknown) =>
    key in fields ? fields[key].trim() || null : (fallback ?? null);
  const seasonRaw = nullable("season_start", result.season_start);
  const season = seasonRaw === null ? null : Number(seasonRaw);
  if (season !== null && (!Number.isInteger(season) || season < 1940 || season > 2200))
    throw new Error("赛季年份应在 1940–2200 之间，未知时可留空");
  const positive = (key: string, fallback: unknown) => {
    const raw = nullable(key, fallback);
    if (raw === null) return null;
    const value = Number(raw);
    if (!Number.isInteger(value) || value <= 0)
      throw new Error("编号必须为正整数");
    return value;
  };
  const serial_number = positive("serial_number", result.serial_number),
    serial_total = positive("serial_total", result.serial_total);
  if (serial_number && serial_total && serial_number > serial_total)
    throw new Error("卡片编号不能大于限编总数");
  const gradeRaw = nullable("grade_value", result.grade_value);
  const grade = gradeRaw === null ? null : Number(gradeRaw);
  if (grade !== null && (!Number.isFinite(grade) || grade < 0 || grade > 10))
    throw new Error("评级分数应在 0–10 之间");
  const endRaw = nullable("season_end", result.season_end);
  const seasonEnd = endRaw === null ? null : Number(endRaw);
  if (
    "season_end" in fields &&
    seasonEnd !== null &&
    season !== null &&
    seasonEnd !== season &&
    seasonEnd !== season + 1
  )
    throw new Error("赛季结束年应为起始年或下一年");
  const flag = (
    key: "is_rookie" | "is_autograph" | "is_memorabilia" | "is_patch" | "has_inscription",
  ) => (key in fields ? (fields[key] === "true" ? true : fields[key] === "false" ? false : null) : (result[key] ?? null));
  return {
    owner_id: owner,
    card: {
      player_name: nullable("player_name", result.player_name),
      brand_name: nullable("brand_name", result.brand_name),
      product_name: nullable("product_name", result.product_name),
      season_start: season,
      season_end: seasonEnd,
      card_number: nullable("card_number", result.card_number),
      parallel_name: nullable("parallel_name", result.parallel_name),
      insert_name: nullable("insert_name", result.insert_name),
      is_rookie: flag("is_rookie"),
      is_autograph: flag("is_autograph"),
      autograph_type: flag("is_autograph")
        ? result.autograph_type === "none"
          ? "unknown"
          : (result.autograph_type ?? "unknown")
        : "none",
      is_memorabilia: flag("is_memorabilia"),
      memorabilia_types: result.memorabilia_types ?? [],
      memorabilia_usage: result.memorabilia_usage ?? "unknown",
      memorabilia_player: result.memorabilia_player ?? null,
      is_patch: flag("is_patch"),
      has_inscription:
        flag("is_autograph") === false ? false : flag("has_inscription"),
      inscription_text: flag("is_autograph")
        ? (result.inscription_text ?? null)
        : null,
      print_run: serial_total,
    },
    serial_number,
    serial_total,
    is_numbered: serial_number || serial_total ? true : (result.is_numbered ?? null),
    grade_company: nullable("grade_company", result.grade_company),
    grade_value: grade,
    notes: "用户确认录入；未知信息保留待确认，识别证据保留在原任务中。",
  };
}
