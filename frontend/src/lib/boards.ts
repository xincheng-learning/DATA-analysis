import {localRequest, type PanelReference} from './api';
import type {RecordValue} from './analysis';

export interface BoardItem {
  id:string;kind:'chart'|'text';title:string;text:string;placed:boolean;
  x:number;y:number;width:number;height:number;spec?:RecordValue;scope?:string;
  appearance?:import('./chart-appearance').ChartAppearance;
}
export interface AnalysisBoard {id:string;title:string;version:number;updated_at:number;items:BoardItem[]}
export type BoardSummary=PanelReference;
export const listBoards=():Promise<BoardSummary[]>=>localRequest('/boards');
export const createBoard=(title:string):Promise<AnalysisBoard>=>localRequest('/boards','POST',{title});
export const readBoard=(id:string):Promise<AnalysisBoard>=>localRequest(`/boards/${id}`);
export const removeBoard=(id:string)=>localRequest(`/boards/${id}`,'DELETE',{});
export const addBoardChart=(id:string,session_id:string,message_id:number,source_path?:(string|number)[]):Promise<AnalysisBoard>=>localRequest(`/boards/${id}/charts`,'POST',{session_id,message_id,source_path});
export const saveBoard=(board:AnalysisBoard):Promise<AnalysisBoard>=>localRequest(`/boards/${board.id}`,'PUT',{
  title:board.title,version:board.version,items:board.items.map(item=>{const saved={...item};delete saved.spec;delete saved.scope;return saved;}),
});
export const boardSummary=(board:AnalysisBoard):BoardSummary=>({id:board.id,title:board.title,version:board.version,
  chart_count:board.items.filter(item=>item.kind==='chart' && item.placed).length,
  note_count:board.items.filter(item=>item.kind==='text' && item.placed).length});
export const snap=(value:number)=>Math.max(0,Math.round(value/8)*8);
