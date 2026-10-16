import React from "react";

type Props = {
  price?: string | null;
  total?: string | null;
  currency?: string;
  detail?: boolean;
};

function purchaseMoney(value: string, currency: string) {
  const [whole, fraction = ""] = value.split(".");
  const amount = `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${fraction.replace(/0+$/, "").padEnd(2, "0")}`;
  return `${currency === "CNY" ? "¥" : `${currency} `}${amount}`;
}

// Null means unknown. Never synthesize a cost from valuation, card price or zero.
export function PurchaseCostValues({price, total, currency = "CNY", detail = false}: Props) {
  if (detail) return total == null ? null : <p>购入总成本：{purchaseMoney(total, currency)}</p>;
  return <>
    {price != null && <strong>{purchaseMoney(price, currency)}</strong>}
    {total != null && <small className="cost-secondary">购入总成本 {purchaseMoney(total, currency)}</small>}
  </>;
}
