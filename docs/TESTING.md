# 测试入口与副作用边界

本说明不表示任何新测试已通过。开发与验证当前暂停；[首补丁状态](SOURCE-STATE.md)为已落地、未GREEN。

## 先确认解释器与源码

2026-10-03 已在指定开发工作树创建 `.venv`：Python 3.13.0，`include-system-site-packages = false`；官方 PyPI wheel 预检和项目 `.[dev]` 安装成功。`pip check`、解释器/pip 路径和 19 个第三方模块导入核查通过。未安装 eval extra、pytest-html-reporter 或额外模型包。实际依赖清单、解析及安装报告保存在独立任务目录。没有 Python 锁文件，本次解析记录不等同于仓库锁文件；历史共享环境及报告保持原样。功能 GREEN、全回归和业务评测仍未运行。

后续测试使用一次性子进程，显式指定工作树cwd/PYTHONPATH和 `-c pyproject.toml`。解释器所属目录、其他checkout或教学snapshot都不能代替指定源码工作树。以下命令中的变量必须先从经确认的本机映射取得，不能把占位当真实路径。

## 已验证环境的安全命令

从指定开发工作树根目录执行，显式使用本项目解释器；不需要激活环境：

```powershell
& .\.venv\Scripts\python.exe -I -B -c "import sys; print(sys.executable); print(sys.version)"
& .\.venv\Scripts\python.exe -I -B -m pip --version
& .\.venv\Scripts\python.exe -I -B -m pip check
```

这些命令只核查环境；不能据此宣称应用测试通过。pytest 仍需下述独立输出、存储和数据库隔离条件；阶段脚本、真实 API、模型调用与数据库迁移仍不自动执行。新环境没有 HTML reporter；下述禁用选项用于保留历史副作用隔离规则。

## pytest配置与可选报告处理

[pyproject.toml](../pyproject.toml)声明默认测试根为 `backend/tests`、`eval_center/tests`，pythonpath为`.`，addopts为`-ra`。没有已确认的Makefile/Justfile入口，不编造make命令。

本机第三方entrypoint已确认：`reporter = pytest_html_reporter.plugin`，发行包 `pytest-html-reporter`。它曾在收尾尝试移动旧 `output.json` 到archive。取消这一非必要写入可在**本次测试进程**使用pytest官方选项 `-p no:reporter`；其余插件、项目测试与保护断言保持启用。不得设置全局autoload禁用、卸载插件或永久改配置。

`-p no:cacheprovider`只取消pytest缓存写入；`-B`/`PYTHONDONTWRITEBYTECODE=1`避免在原源码写pycache。每轮 `--basetemp` 应使用独立产物目录下的新唯一子目录，避免pytest清理旧证据。

后续单测命令模板，**本次整理没有执行**：

```powershell
# 仅在一次性子进程内，先确认三个变量；不是用户服务终端。
# $VerifiedPython：已验证解释器
# $VerifiedWorktree：指定开发工作树
# $IndependentArtifacts：本次独立产物根
Set-Location -LiteralPath $VerifiedWorktree
$env:PYTHONPATH = (Get-Location).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:RAG_CLOUD_ENABLED = '0'
$env:RAG_LOCAL_ANSWER_ENABLED = '0'
$env:RAG_LOCAL_QUERY_ENABLED = '0'
$env:RAG_DATABASE_URL = 'sqlite+pysqlite:///:memory:'
$runDir = Join-Path $IndependentArtifacts ('pytest-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $runDir | Out-Null
$env:RAG_STORAGE_ROOT = Join-Path $runDir 'storage'
& $VerifiedPython -B -m pytest -c pyproject.toml `
  -p no:reporter -p no:cacheprovider `
  --basetemp (Join-Path $runDir 'tmp') `
  backend/tests/test_context_pool_policy.py -q --tb=short `
  *> (Join-Path $runDir 'stdout-stderr.txt')
# 保留实际 $LASTEXITCODE；不能将RED或未运行结果记为PASS。
```

先验证当前新用例和受影响回归；完整两测试根的aggregate需另行核查用例副作用与授权，不能由“裸pytest”获得真实DB、API或模型权限。历史608 passed不表示当前补丁回归。

## 脚本分类

| 入口 | 已知行为与条件 |
|---|---|
| [verify-m0.ps1](../scripts/verify-m0.ps1) | 包含pytest，也调用模型探测/schema；不是纯离线单测 |
| [verify-m1.ps1](../scripts/verify-m1.ps1) | 包含迁移、真实模型/DB/API smoke；需独立授权 |
| [verify-m4.ps1](../scripts/verify-m4.ps1) | 包含Agent API smoke；不能作为本轮无服务测试 |
| [schema_check.ps1](../scripts/schema_check.ps1) | 可启动Compose DB并迁移；整理期间不运行，也不调查Docker权限 |
| [contract_test.ps1](../scripts/contract_test.ps1)、[eval.ps1](../scripts/eval.ps1) | 有各自输入、输出和评测语义；先确认模式和副作用，不等同当前真实质量验收 |
| [verify-release.ps1](../scripts/verify-release.ps1) | 当前原生发布入口与旧调用不同；保留其拒绝规则，禁止直接照抄历史Fresh重建 |

失败、跳过、未运行和真实通过分别记录。若测试需要修改引用处理、成本保护或其他范围，先报告，不临时放松断言。见 [已知问题](KNOWN-ISSUES.md) 和 [产物保护规范](ARTIFACTS.md)。
