# Chart24 / 24种基础图形开发参考集

版本1.0.0；固定SM01—SM24，对应[基础图形矩阵](../../docs/Tableau基础图形覆盖.md)。原创小规模合成输入，继承项目根目录MIT许可；未复制Tableau或第三方基准的题目、图像和代码，不构成Tableau认证。七类既有参考复用项目的`tableau_reference_v1/families.json`，其原标准不改。

这是公开开发集，已用于发现问题和调整产品，不能作为盲测。真实模型选图、固定输入构图、独立数值评分、页面交互、人工看图与完整AI分析分别计分。当前记录见[续验报告](../../docs/40元续验与24图形复验-2026-11-15.md)和[公开摘要](RESULTS_20261115.json)；24种图形通过不代表24个完整AI分析任务通过。

## 参考与评分

- `../chart24_reference.py`只使用独立字面数值、标准库和Matplotlib绘教师，不导入产品计算或绘图；`manifest.json`冻结参考源码、旧families及全部输入/预期的哈希。
- `../chart24_scoring.py`核对实际构图的数据、结构、单位、空值、零、重复、层级及统计口径；逐图修改一个实际绘图数值作为反例。24个反例全部识别也不能证明所有错误都能识别。
- 箱线：linear四分位、1.5IQR范围内实际值作须，主体放大与含100离群值的全量视图同时保留。直方：8个有效观测、1个未知；允许不同的已说明分箱，但不能丢零、去重或遗漏观测。
- 时间缺口不补零、不跨空值涂色；双轴保留各自单位；地图仅使用给定位置/示意边界；气泡比较面积1:4:9与零，不要求相同排圆布局；树图比较完整路径、叶子与父节点合计，不要求相同矩形布局。
- 视觉验收需将独立教师与实际页面导出并排人工审阅，不能只检查PNG存在或尺寸正确。CSV按完整原始行独立解析；PNG另检查实际像素，曾因此发现气泡导出的重叠及裁切。

## 复现

在项目根目录使用PowerShell 7与现有依赖。先验证冻结资产：

```powershell
& '.venv/Scripts/python.exe' -X utf8 -m pytest backend/tests/test_chart24_acceptance.py -q
```

教师图片需要已安装Matplotlib、NumPy的Python环境。本机使用已有Conda，路径仅为本机示例：

```powershell
& 'D:/CondaEnvs/ml-pytorch/python.exe' -X utf8 evals/chart24_reference.py --output output/my-chart24-teacher
```

当前参考绘图显式使用Windows微软雅黑`C:/Windows/Fonts/msyh.ttc`，其他系统的字体与渲染尚未验证。若需改字体或参考源码，必须另建有变更说明的版本；禁止为了让产品通过而覆盖本版本哈希。数值冻结检查不依赖该字体。

已有真实模型选图记录时可离线评分，不计费；替换示例路径，使用不存在的评分文件名：

```powershell
& '.venv/Scripts/python.exe' -X utf8 scripts/score_chart24_eval.py --run evals/runs/gold-v1/my-chart24-run --reference output/my-chart24-teacher/reference.json --output output/my-chart24-score.json
```

新选图运行会计费，需显式`--live`；只支持已核价模型，继续原共享累计40元账本，不重置或改变真实模型设置。SM01—SM23走产品选图节点，SM24先取得真实ViewRequest，再运行生产TypeScript层级构图器：

```powershell
& '.venv/Scripts/python.exe' -X utf8 scripts/run_chart24_eval.py --live --reference output/my-chart24-teacher/reference.json --output evals/runs/gold-v1/my-chart24-run
node scripts/compile_chart24_advanced.cjs evals/runs/gold-v1/my-chart24-run
```

完整应用复验复用已经存在的独立QA服务（8101/3100）。`seed_chart24_qa.py`只允许项目`.local/qa-*`目录，并核对API与数据库，禁止真实聊天目录；每轮使用新输出。`check_chart24_browser.cjs`需已有Playwright及Edge，不会自动安装。它逐图操作保存、重开、明暗、窄屏全屏和UI下载PNG；CSV由真实HTTP下载接口取得，未冒称点击CSV按钮。`verify_chart24_exports.py`另需已有Pillow/SciPy。具体命令与本机证据位置见报告。

## 发布边界

公开源码、合成输入、冻结标准和不含私有信息的结果摘要；原始模型请求、聊天库、QA会话、费用逐条账本和浏览器产物保留在被Git忽略的`evals/runs`、`.local`、`output`。公开摘要的原始记录哈希用于比对，不能替代第三方独立重现。未覆盖大表、全部缺失模式、不同语言/行业、跨浏览器及端到端AI分析质量。
