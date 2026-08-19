import { html } from "/lib.js";

// The master pipeline flow-graph is a scoped React/React Flow app served at /pipeline/.
// It is embedded via an iframe for clean isolation from the no-build Preact shell.
export function PipelineView() {
  return html`<section class="view" style="padding:0">
    <iframe src="/pipeline/" title="Pipeline"
            style="width:100%;height:calc(100vh - 20px);border:0;display:block;background:var(--bg-0)"></iframe>
  </section>`;
}
