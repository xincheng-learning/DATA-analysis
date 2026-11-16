import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import vm from "node:vm";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

// Compile the real component without starting a browser or adding a test framework.
const filename = fileURLToPath(new URL("../src/components/chat-message.tsx", import.meta.url));
const compiled = { exports: {} };
const { outputText } = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
});
vm.runInNewContext(outputText, {
  exports: compiled.exports, module: compiled, require: (name) => ({
    "@/components/analysis-evidence": { AnalysisEvidence: () => null },
    "@/components/analysis-narrative": { AnalysisNarrative: () => null },
    "@/components/chart-view": { ChartView: () => null },
    "@/components/data-panel": { ResultTable: () => null },
    "@/lib/analysis": { compactAnswer: () => null },
    "@/lib/delivery-trust": { continuationDrafts: () => [] },
    "@/components/message-markdown": { MessageMarkdown: ({content}) => content },
    "@/components/data-overview": { DataOverview: () => null },
    "@/components/cleaning-card": { CleaningCard: () => null },
    "@/lib/api": { imageUrl: (sid,id) => `/sessions/${sid}/images/${id}` },
    "@/components/clarification-card":{ClarificationCard:()=>null},
    "@/components/sql-proposal-card":{SqlProposalCard:()=>null},
    "@/components/verification-checks":{VerificationChecks:()=>null,VerificationWarnings:()=>null,DeliveryTrustPanel:()=>null},
    "@/components/metric-context-card":{MetricContextCard:()=>null},
  }[name] || createRequire(filename)(name)),
});
const { ChatMessage } = compiled.exports;

test('sent images keep their filename and same-chat source link',()=>{
  const id='a'.repeat(64)+'.png';
  const html=renderToStaticMarkup(React.createElement(ChatMessage,{role:'user',sessionId:'image-chat',content:'检查 SQL',metadata:{context_snapshot:{images:[{id,filename:'sql.png'}]}}}));
  assert.ok(html.includes(`/sessions/image-chat/images/${id}`));
  assert.ok(html.includes('alt="sql.png"'));
  assert.ok(html.includes('本轮图片附件'));
});

test("legacy upload notices display in Chinese without changing filenames", () => {
  const message = Object.freeze({
    role: "user", content: "📎 Uploaded: 销售.csv, sales.xlsx",
    metadata: Object.freeze({ type: "upload" }),
  });
  const html = renderToStaticMarkup(React.createElement(ChatMessage, message));
  assert.ok(html.includes("📎 已上传：销售.csv, sales.xlsx"));
  assert.ok(!html.includes("Uploaded:"));
  assert.equal(message.content, "📎 Uploaded: 销售.csv, sales.xlsx");
});

test("normal conversation text and Chinese upload notices are preserved", () => {
  for (const message of [
    { role: "user", content: "📎 Uploaded: 用户原文.csv" },
    { role: "assistant", content: "Uploaded: 模型原文.csv" },
    { role: "user", content: "📎 已上传：销售.csv", metadata: { type: "upload" } },
  ]) {
    const html = renderToStaticMarkup(React.createElement(ChatMessage, message));
    assert.ok(html.includes(message.content));
  }
});
