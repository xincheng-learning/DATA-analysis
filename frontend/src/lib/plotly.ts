import Plotly from "plotly.js/lib/core";
import scatter from "plotly.js/lib/scatter";
import bar from "plotly.js/lib/bar";
import histogram from "plotly.js/lib/histogram";
import heatmap from "plotly.js/lib/heatmap";
import pie from "plotly.js/lib/pie";
import waterfall from "plotly.js/lib/waterfall";
import funnel from "plotly.js/lib/funnel";
import treemap from 'plotly.js/lib/treemap';
import sunburst from 'plotly.js/lib/sunburst';
import sankey from 'plotly.js/lib/sankey';
import box from 'plotly.js/lib/box';
import table from 'plotly.js/lib/table';
import scattermap from 'plotly.js/lib/scattermap';
import choroplethmap from 'plotly.js/lib/choroplethmap';

// Bundled and source traces have separate registries; all supported types share this core.
Plotly.register([scatter, bar, histogram, heatmap, pie, waterfall, funnel, treemap, sunburst, sankey, box, table, scattermap, choroplethmap]);
export default Plotly;
