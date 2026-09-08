# 测试

```bash
python3 -m unittest discover -s tests -q
```

测试套件位于仓库根 `tests/`。新增命令建议补对应单元测试，业务逻辑在 `lib/{名}.py` 下测，薄壳仅做透传无需单独测。`tests/test_lazyhelp.py` 会校验 bin/ 入口与 `TOOLS` 注册表一致，加完入口记得注册。
