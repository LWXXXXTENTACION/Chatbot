import { runSSEPerformanceEval } from "../src/lib/sse-eval.ts";

const result = await runSSEPerformanceEval();

console.table([
  {
    version: "before: per-delta",
    pipeline_ms: result.legacy.pipelineMs,
    publications: result.legacy.publications,
  },
  {
    version: "after: rAF double-buffer",
    pipeline_ms: result.optimized.pipelineMs,
    publications: result.optimized.publications,
  },
]);
console.log(JSON.stringify(result, null, 2));

if (
  !result.protocol.parserPass
  || !result.protocol.resumePass
  || !result.robustness.corruptFramePass
  || !result.robustness.watchdogPass
  || !result.robustness.schedulerPass
  || !result.robustness.sessionPersistencePass
  || !result.robustness.unicodeBufferPass
  || !result.robustness.streamingTextIntegrityPass
  || !result.robustness.duplicateReplayPass
  || !result.robustness.artifactRestorePass
  || result.comparison.publicationReductionPct < 95
) {
  process.exitCode = 1;
}
