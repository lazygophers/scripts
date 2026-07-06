# 测试

```bash
python3 -m unittest discover -s tests -q
```

测试套件位于仓库根 `tests/`。新增命令建议补对应单元测试，保持薄壳与业务分离的测试粒度（业务逻辑在 `lib/commands/` 下测，薄壳仅做透传无需单独测）。
