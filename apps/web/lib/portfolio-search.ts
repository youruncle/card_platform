export type SearchFilters = {
  q?: string;
  player?: string[]; brand?: string[]; product?: string[]; insert?: string[];
  parallel?: string[]; card_number?: string[]; grade_company?: string[];
  valuation_status?: string[];
  year_min?: number; year_max?: number; serial_number?: number; serial_min?: number; serial_max?: number;
  grade_min?: number; grade_max?: number; cost_min?: number; cost_max?: number; value_min?: number; value_max?: number;
  rookie?: boolean; autograph?: boolean; memorabilia?: boolean; numbered?: boolean; graded?: boolean; has_cost?: boolean;
};

export const textFilters = [
  ['player','球员','杜兰特、科比（用逗号分隔）'],['brand','品牌','Topps, Panini'],
  ['product','产品系列','Chrome, Mosaic'],['insert','特卡 / Insert','Oasis, 教堂'],
  ['parallel','平行版本','Gold, 黑白'],['card_number','卡号','157, O-15'],
  ['grade_company','评级公司','PSA, BGS'],
] as const;
export const boolFilters = [
  ['rookie','新秀 RC'],['autograph','签字'],['memorabilia','物料'],['numbered','带编'],
  ['graded','已评级'],['has_cost','成本已录入'],
] as const;
export const numberFilters = [
  ['year_min','起始年份',1800,2200,1],['year_max','结束年份',1800,2200,1],
  ['serial_number','实编号码',1,undefined,1],['serial_min','总限编最小值',1,undefined,1],['serial_max','总限编最大值',1,undefined,1],
  ['grade_min','最低评级分数',0,10,.5],['grade_max','最高评级分数',0,10,.5],
  ['cost_min','最低成本',0,undefined,.01],['cost_max','最高成本',0,undefined,.01],
  ['value_min','最低正式估值',0,undefined,.01],['value_max','最高正式估值',0,undefined,.01],
] as const;
export const valuationChoices=[['valued','已正式估值'],['unvalued','未正式估值'],['queued','等待估值'],['running','估值中'],['failed','估值失败'],['insufficient_evidence','证据不足']] as const;
export const filterLabels:Record<string,string>=Object.fromEntries([
  ['q','关键词'],['valuation_status','估值状态'],...textFilters.map(([k,l])=>[k,l]),
  ...boolFilters,...numberFilters.map(([k,l])=>[k,l]),
]);

export function searchDraft(filters:SearchFilters):Record<string,string> {
  return Object.fromEntries(Object.entries(filters).map(([k,v])=>[k,Array.isArray(v)?v.join(', '):String(v)]));
}

export function parseSearchDraft(draft:Record<string,string>):SearchFilters {
  const result:Record<string,unknown>={};
  if(draft.q?.trim())result.q=draft.q.trim();
  if(String(result.q??'').length>160)throw new Error('关键词最多 160 个字符');
  for(const [key] of textFilters){
    const values=[...new Set((draft[key]??'').split(/[,，、;；]/).map(v=>v.trim()).filter(Boolean))];
    if(values.length>20 || values.some(v=>v.length>80))throw new Error('每项最多 20 个值，每个值最多 80 个字符');
    if(values.length)result[key]=values;
  }
  for(const [key] of boolFilters){if(draft[key]==='true'||draft[key]==='false')result[key]=draft[key]==='true';}
  for(const [key,label,min,max,step] of numberFilters){
    if(!draft[key]?.trim())continue;
    const value=Number(draft[key]);
    if(!Number.isFinite(value)||value<min||(max!==undefined&&value>max)||(step===1&&!Number.isInteger(value)))throw new Error(`${label}超出有效范围`);
    result[key]=value;
  }
  for(const prefix of ['year','serial','grade','cost','value']){
    const min=result[prefix+'_min'],max=result[prefix+'_max'];
    if(typeof min==='number'&&typeof max==='number'&&min>max)throw new Error(`${filterLabels[prefix+'_min']}：最小值不能大于最大值`);
  }
  const statuses=(draft.valuation_status??'').split(',').filter(Boolean);
  if(statuses.length)result.valuation_status=statuses;
  return result as SearchFilters;
}

export function searchQuery(filters:SearchFilters):string {
  return Object.keys(filters).length ? `&search=${encodeURIComponent(JSON.stringify(filters))}` : '';
}
