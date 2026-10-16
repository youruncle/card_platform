/** Server sorts the full final projection; clients never sort a page locally. */
export type PortfolioSortBy = 'created_at' | 'player_name' | 'season_start' | 'remaining_cost' | 'fair_value' | 'unrealized_profit' | 'relevance';
export type PortfolioSortOrder = 'asc' | 'desc';
export type PortfolioSort = { sort_by: PortfolioSortBy; order: PortfolioSortOrder };
export const PORTFOLIO_SORT_OPTIONS: ReadonlyArray<{value: PortfolioSortBy; label: string}> = [
  {value:'created_at',label:'录入时间'},
  {value:'player_name',label:'球员名称'},
  {value:'season_start',label:'赛季'},
  {value:'remaining_cost',label:'持仓成本'},
  {value:'fair_value',label:'正式估值'},
  {value:'unrealized_profit',label:'未实现盈亏'},
  {value:'relevance',label:'相关度'},
];
export const DEFAULT_PORTFOLIO_SORT: PortfolioSort = {sort_by:'created_at',order:'desc'};
export function portfolioSortQuery(sort: PortfolioSort, snapshotToken?: string | null): string {
  const params = new URLSearchParams(sort);
  if (snapshotToken) params.set('snapshot_token',snapshotToken);
  else params.set('snapshot','true');
  return `&${params}`;
}
