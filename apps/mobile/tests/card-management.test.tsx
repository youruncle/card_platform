import React from 'react';
import {afterEach,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render as baseRender,screen,waitFor} from '@testing-library/react';
import {CardManagement} from '../src/CardManagement';
import type {MobileApi} from '../src/api';

function render(...args:Parameters<typeof baseRender>){const result=baseRender(...args);result.container.querySelectorAll('details').forEach(el=>{el.open=true;});return result;}
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.useRealTimers();});
const identity={player_name:'Original',season_start:2020,season_end:null,brand_name:'Brand',product_name:'Product',set_name:null,card_number:null,insert_name:null,parallel_name:null,serial_number:null,serial_total:null,is_rookie:false,is_autograph:false,autograph_type:'unknown',is_memorabilia:false,memorabilia_types:[],grade_company:null,grade_value:null};
function setup(overrides:Record<string,unknown>={}){
 const get=vi.fn(async(path:string)=>path.includes('identity-review')?{current_identity:identity,candidates:[],field_provenance:{},conflicts:['未知平行']}:path.includes('automated-valuation')?{status:null}:{item:{holding_status:'held'},transactions:[],valuations:[]});
 const post=vi.fn().mockResolvedValue({}),request=vi.fn().mockResolvedValue(undefined),onChanged=vi.fn(),onDeleted=vi.fn();
 const props={api:{get,post,request,...overrides} as unknown as MobileApi,id:'a',ownerId:'owner',baseCurrency:'CNY',onChanged,onDeleted};
 return {get,post,request,onChanged,onDeleted,props};
}
it('blocks valuation before loading, prevents running duplicates and polls until terminal',async()=>{
 vi.useFakeTimers();let resolve!:(v:unknown)=>void;let count=0;
 const get=vi.fn((path:string)=>path.includes('automated-valuation')?(++count===1?new Promise(r=>resolve=r):Promise.resolve({status:'succeeded'})):Promise.resolve({}));
 const s=setup({get});const view=render(<CardManagement {...s.props}/>);
 expect((screen.getByRole('button',{name:'发起/重试估值'}) as HTMLButtonElement).disabled).toBe(true);
 await act(async()=>resolve({status:'running'}));
 expect((screen.getByRole('button',{name:'发起/重试估值'}) as HTMLButtonElement).disabled).toBe(true);
 await act(async()=>{await vi.advanceTimersByTimeAsync(3000);});
 expect(screen.getByText(/估值状态：已完成/)).toBeTruthy();expect(s.onChanged).toHaveBeenCalled();
 view.unmount();await act(async()=>{await vi.advanceTimersByTimeAsync(10000);});expect(count).toBe(2);
});
it('retains identity input on failure and submits only genuine user fields and provenance',async()=>{
 const s=setup();s.post.mockRejectedValueOnce(new Error('保存失败'));render(<CardManagement {...s.props}/>);
 await screen.findByDisplayValue('Original');fireEvent.change(screen.getByLabelText('球员'),{target:{value:'Corrected'}});fireEvent.change(screen.getByLabelText('确认理由'),{target:{value:'背面文字'}});
 fireEvent.click(screen.getByRole('button',{name:'确认身份'}));await screen.findByText('保存失败');expect(screen.getByDisplayValue('Corrected')).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'确认身份'}));await waitFor(()=>expect(s.onChanged).toHaveBeenCalled());
 const payload=s.post.mock.calls[1][1];expect(payload.identity).toEqual({...identity,player_name:'Corrected'});expect(payload.reason).toBe('背面文字');expect(payload.field_provenance.player_name).toEqual({source:'manual',reason:'背面文字'});
});
it('records schema-correct transactions and reloads server history; updates holding and deletes with ownership',async()=>{
 const s=setup();vi.spyOn(window,'confirm').mockReturnValue(true);render(<CardManagement {...s.props}/>);
 await screen.findByDisplayValue('Original');fireEvent.change(screen.getByLabelText('交易日期'),{target:{value:'2026-10-17T10:00'}});fireEvent.change(screen.getByLabelText('单价'),{target:{value:'80'}});
 fireEvent.click(screen.getByRole('button',{name:'保存交易'}));await waitFor(()=>expect(s.post).toHaveBeenCalled());expect(s.post.mock.calls[0][0]).toBe('/v1/portfolio/items/a/transactions?owner_id=owner');expect(s.post.mock.calls[0][1]).toMatchObject({transaction_type:'buy',quantity:'1',unit_price:'80',fee_amount:'0',currency:'CNY',fx_rate_to_base:'1'});
 await waitFor(()=>expect(s.onChanged).toHaveBeenCalledTimes(1));fireEvent.change(screen.getByLabelText('持仓状态'),{target:{value:'sold'}});fireEvent.click(screen.getByRole('button',{name:'保存持仓'}));await waitFor(()=>expect(s.onChanged).toHaveBeenCalledTimes(2));
 expect(s.request.mock.calls[0][1]).toMatchObject({method:'PATCH',body:JSON.stringify({holding_status:'sold'})});
 fireEvent.click(screen.getByRole('button',{name:'移入回收站'}));await waitFor(()=>expect(s.onDeleted).toHaveBeenCalledOnce());expect(s.request.mock.calls[1][1]).toMatchObject({method:'DELETE',body:JSON.stringify({owner_id:'owner',reason:null})});
});
it('keeps failed transactions and holding edits retryable and does not delete after failure',async()=>{
 const s=setup();s.post.mockRejectedValue(new Error('交易离线'));s.request.mockRejectedValue(new Error('更新离线'));vi.spyOn(window,'confirm').mockReturnValue(true);render(<CardManagement {...s.props}/>);await screen.findByDisplayValue('Original');
 fireEvent.change(screen.getByLabelText('交易日期'),{target:{value:'2026-10-17T10:00'}});fireEvent.change(screen.getByLabelText('单价'),{target:{value:'42'}});fireEvent.click(screen.getByRole('button',{name:'保存交易'}));await screen.findByText('交易离线');expect(screen.getByDisplayValue('42')).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'保存持仓'}));await screen.findByText('更新离线');fireEvent.click(screen.getByRole('button',{name:'移入回收站'}));await waitFor(()=>expect(s.request).toHaveBeenCalledTimes(2));expect(s.onDeleted).not.toHaveBeenCalled();expect(s.onChanged).not.toHaveBeenCalled();
});
it('keeps candidate evidence until manual changes and never confirms a candidate automatically',async()=>{
 const candidate={id:'c',is_valid:true,identity:{...identity,parallel_name:'Gold'},field_provenance:{parallel_name:{source:'checklist',url:'https://example.com'}},confidence:'0.8',validation_reasons:['serial unclear']};
 const s=setup({get:vi.fn(async(path:string)=>path.includes('identity-review')?{current_identity:identity,candidates:[candidate]}:{})});render(<CardManagement {...s.props}/>);
 await screen.findByRole('button',{name:'选择候选 1'});expect(s.post).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'选择候选 1'}));expect(screen.getByDisplayValue('Gold')).toBeTruthy();
 fireEvent.change(screen.getByLabelText('确认理由'),{target:{value:'核对清单'}});fireEvent.click(screen.getByRole('button',{name:'确认身份'}));await waitFor(()=>expect(s.post).toHaveBeenCalled());expect(s.post.mock.calls[0][1].field_provenance.parallel_name).toEqual(candidate.field_provenance.parallel_name);
});
it('ignores late results and callbacks after switching cards',async()=>{
 let resolve!:(v:unknown)=>void;const s=setup();s.post.mockImplementationOnce(()=>new Promise(r=>resolve=r));const view=render(<CardManagement {...s.props}/>);await screen.findByDisplayValue('Original');
 fireEvent.change(screen.getByLabelText('确认理由'),{target:{value:'核对身份'}});fireEvent.click(screen.getByRole('button',{name:'确认身份'}));view.rerender(<CardManagement {...s.props} id="b"/>);await act(async()=>resolve({}));expect(s.onChanged).not.toHaveBeenCalled();
});
it('recovers valuation reads after failure and supports starting a queued job',async()=>{
 let reads=0;const s=setup({get:vi.fn(async(path:string)=>{if(path.includes('automated-valuation')){reads++;if(reads===1)throw new Error('估值离线');return {status:reads===2?null:'queued'};}return {};})});render(<CardManagement {...s.props}/>);await screen.findByText('估值离线');expect((screen.getByRole('button',{name:'发起/重试估值'}) as HTMLButtonElement).disabled).toBe(true);
 fireEvent.click(screen.getByRole('button',{name:'刷新估值状态'}));await screen.findByText('估值状态：尚未发起');fireEvent.click(screen.getByRole('button',{name:'发起/重试估值'}));await screen.findByText('估值状态：排队中');expect(s.post).toHaveBeenCalledTimes(1);expect((screen.getByRole('button',{name:'发起/重试估值'}) as HTMLButtonElement).disabled).toBe(true);
});
it('can load the identity editor after an initial network failure',async()=>{
 let failed=false;const s=setup({get:vi.fn(async(path:string)=>{if(path.includes('identity-review')){if(!failed){failed=true;throw new Error('身份离线');}return {current_identity:identity,candidates:[]};}return {};})});render(<CardManagement {...s.props}/>);await screen.findByText('身份离线');fireEvent.click(screen.getByRole('button',{name:'刷新身份审核'}));await screen.findByDisplayValue('Original');
});
it('does not allow saving the same completed transaction twice without entering a new date and price',async()=>{
 const s=setup();render(<CardManagement {...s.props}/>);await screen.findByDisplayValue('Original');expect((screen.getByRole('button',{name:'保存交易'}) as HTMLButtonElement).disabled).toBe(true);
 fireEvent.change(screen.getByLabelText('交易日期'),{target:{value:'2026-10-17T10:00'}});fireEvent.change(screen.getByLabelText('单价'),{target:{value:'12'}});fireEvent.click(screen.getByRole('button',{name:'保存交易'}));await screen.findByText('交易已保存');fireEvent.click(screen.getByRole('button',{name:'保存交易'}));expect(s.post).toHaveBeenCalledTimes(1);
});
it('does not let a stale polling response overwrite a newer manual terminal read',async()=>{
 vi.useFakeTimers();let reads=0,resolve!:(v:unknown)=>void;const s=setup({get:vi.fn((path:string)=>{if(!path.includes('automated-valuation'))return Promise.resolve({});reads++;return reads===1?Promise.resolve({status:'running'}):reads===2?new Promise(r=>resolve=r):Promise.resolve({status:'succeeded'});})});render(<CardManagement {...s.props}/>);await act(async()=>{});await act(async()=>{await vi.advanceTimersByTimeAsync(2500);});fireEvent.click(screen.getByRole('button',{name:'刷新估值状态'}));await act(async()=>{});await act(async()=>resolve({status:'running'}));expect(screen.getByText('估值状态：已完成')).toBeTruthy();await act(async()=>{await vi.advanceTimersByTimeAsync(5000);});expect(reads).toBe(3);
});
it.each(['identifying','searching','calculating'])('keeps real worker stage %s running and polls without duplicate submissions',async(stage)=>{
 vi.useFakeTimers();let reads=0;const s=setup({get:vi.fn(async(path:string)=>path.includes('automated-valuation')?{status:++reads===1?stage:'succeeded'}:{})});render(<CardManagement {...s.props}/>);await act(async()=>{});expect((screen.getByRole('button',{name:'发起/重试估值'}) as HTMLButtonElement).disabled).toBe(true);await act(async()=>{await vi.advanceTimersByTimeAsync(2500);});expect(screen.getByText('估值状态：已完成')).toBeTruthy();expect(s.post).not.toHaveBeenCalled();
});
it('consumes a successful transaction even when subsequent history refresh fails',async()=>{
 let detailReads=0;const s=setup({get:vi.fn(async(path:string)=>{if(path.includes('identity-review'))return {current_identity:identity,candidates:[]};if(path.includes('/detail')&&++detailReads>1)throw new Error('历史离线');return {};})});render(<CardManagement {...s.props}/>);await screen.findByDisplayValue('Original');fireEvent.change(screen.getByLabelText('交易日期'),{target:{value:'2026-10-17T10:00'}});fireEvent.change(screen.getByLabelText('单价'),{target:{value:'12'}});fireEvent.click(screen.getByRole('button',{name:'保存交易'}));await screen.findByText('已保存，历史刷新失败请刷新');expect(s.onChanged).toHaveBeenCalledOnce();expect((screen.getByRole('button',{name:'保存交易'}) as HTMLButtonElement).disabled).toBe(true);fireEvent.click(screen.getByRole('button',{name:'保存交易'}));expect(s.post).toHaveBeenCalledTimes(1);
});
it('reports successful identity confirmation separately from a failed refresh',async()=>{
 let reviewReads=0;const s=setup({get:vi.fn(async(path:string)=>{if(path.includes('identity-review')){if(++reviewReads>1)throw new Error('身份读取失败');return {current_identity:identity,candidates:[]};}return {};})});render(<CardManagement {...s.props}/>);await screen.findByDisplayValue('Original');fireEvent.change(screen.getByLabelText('确认理由'),{target:{value:'背面文字'}});fireEvent.click(screen.getByRole('button',{name:'确认身份'}));await screen.findByText('身份已确认，资料刷新失败请刷新');expect(s.onChanged).toHaveBeenCalledOnce();expect(s.post).toHaveBeenCalledTimes(1);
});
