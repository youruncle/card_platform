/** Supplement acceptance preserves the complete current identity, including fields
 * outside the initial set of supported suggestions. No unknown is a clearing edit. */
export type Identity = Record<string, unknown>;
export type IdentitySuggestion = {key:string;label:string;current:unknown;suggested:unknown};
export const supplementIdentityLabels:Record<string,string> = {
 player_name:'球员',brand_name:'品牌',season_start:'赛季起始年',season_end:'赛季结束年',
 product_name:'产品',set_name:'系列',card_number:'卡号',insert_name:'特卡',parallel_name:'平行',
};
export function identitySuggestions(current:Identity, recognition:object|null|undefined):IdentitySuggestion[]{
 return Object.entries(supplementIdentityLabels).flatMap(([key,label])=>{
  const suggested=(recognition as Identity|null)?.[key];
  if(suggested===null||suggested===undefined||suggested===''||(typeof suggested==='string'&&['','unknown','未知'].includes(suggested.trim().toLowerCase())))return [];
  if(JSON.stringify(suggested)===JSON.stringify(current[key]))return [];
  return [{key,label,current:current[key],suggested}];
 });
}
export function acceptIdentitySuggestions(current:Identity,suggestions:IdentitySuggestion[],selectedKeys:string[]):Identity{
 const merged:Identity={...current};
 for(const key of new Set(selectedKeys)){
  const suggestion=suggestions.find(s=>s.key===key);
  if(!suggestion||!(key in supplementIdentityLabels))throw Error('该身份字段不能通过补拍接受');
  merged[key]=suggestion.suggested;
 }
 if(merged.season_end!==null&&merged.season_end!==undefined&&merged.season_end!==merged.season_start&&merged.season_end!==Number(merged.season_start)+1)throw Error('请同时核对赛季起始年和结束年');
 return merged;
}
