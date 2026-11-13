/**
 * 零依赖工具：任一开关为真 → true。全关时上游必须一次 python 都不调（#50）。
 *
 * 这个文件禁止 import 任何业务模块——它是被 expression-inject /
 * expression-refresh / safety-gate 三处共用的底层判断，一旦引入业务依赖就会成环。
 */
export function anySwitchOn(switches) {
  return Object.values(switches || {}).some(
    (v) => v === true || (typeof v === 'number' && v > 0))
}
