import {record,type RecordValue} from './analysis';

export interface ChartAppearance {
  title?:string;x_label?:string;y_label?:string;
  palette?:'jade'|'ocean'|'sunset';font_size?:number;show_legend?:boolean;
}
export const chartPalettes={
  jade:['#17856b','#3d92b3','#ba8c31','#9473b8','#b45d73'],
  ocean:['#245b82','#347fa0','#419da5','#73b5b0','#aacdc3'],
  sunset:['#c77745','#ab5680','#6c78b9','#259a91','#a1a748'],
};

export function applyChartAppearance(spec:RecordValue,appearance:ChartAppearance):RecordValue {
  const layout=record(spec.layout),xaxis=record(layout.xaxis),yaxis=record(layout.yaxis);
  return {...spec,presentation:appearance,layout:{...layout,
    ...(appearance.title?.trim()?{title:{...record(layout.title),text:appearance.title.trim()}}:{}),
    ...(appearance.x_label?.trim()?{xaxis:{...xaxis,title:{...record(xaxis.title),text:appearance.x_label.trim()}}}:{}),
    ...(appearance.y_label?.trim()?{yaxis:{...yaxis,title:{...record(yaxis.title),text:appearance.y_label.trim()}}}:{}),
  }};
}
