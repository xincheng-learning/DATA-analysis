declare module "plotly.js/lib/core" {
  import * as Plotly from "plotly.js";
  export default Plotly;
}

declare module "plotly.js/lib/*" {
  const trace: Exclude<Parameters<typeof import("plotly.js").register>[0], unknown[]>;
  export default trace;
}
