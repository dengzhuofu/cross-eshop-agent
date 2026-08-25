# shopverse —— Mock 商城（M12）

没有真实亚马逊/Shopify/TikTok Shop 的 API 与店铺资质，也能**看见铺货效果**：
这是本地扮演的电商站，cross-eshop-agent 的 `publish_listing` 适配器会把上架物
POST 进来，这里以三张页面呈现——

| 页面 | 地址 | 内容 |
| --- | --- | --- |
| Storefront | http://127.0.0.1:8001/ | 商品卡片网格 + 搜索 + 平台 tab（Amazon/Shopify/TikTok Shop） |
| 商品详情 | `/product/{listing_id}` | Amazon 式三栏：主图 / 五点描述+claim / Buy Box |
| Seller Central | `/seller` | 上架清单表：ID / 平台徽标 / 价格 / 状态 / 来源工作流 |

## 运行

零新依赖——backend 的 venv 直接跑（fastapi + uvicorn 已有）：

```bash
python mock-marketplace/server.py --demo   # 空库启动并种 3 条演示商品
# 或
python mock-marketplace/server.py          # 只服务 agent 真实铺进来的商品
```

后端侧无需配置：适配器默认把上架物推到 `http://127.0.0.1:8001`
（`backend/.env` 里 `MOCK_MARKETPLACE_URL` 可改/置空禁用；商城不在线时
推送静默失败，工作流不受影响）。

跑一次真实工作流（或 `scripts/reset_and_replay.sh`）后，storefront 与
Seller Central 就会出现刚铺货的商品；详情页 URL 也会写回工作流 publish
步骤，前端详情页可直接点过去。

## 设计约束

- **幂等 upsert**：按 `listing_id` 主键覆盖写入，适配器/ToolExecutor 的
  幂等重放不会产生重复商品；
- **零外网**：占位主图是确定性 SVG（标题 hash → 渐变 + 首字母），样式本地
  内置，离线可复现；
- **零新依赖**：FastAPI + uvicorn + stdlib sqlite3，f-string 模板不引模板引擎；
- **不阻塞主链路**：商城只是演示出口，推送失败只打 warning 日志。

## 测试

```bash
pytest mock-marketplace -q    # TestClient 进程内全链路，临时 SQLite，零端口
ruff check mock-marketplace
```

CI（.github/workflows/ci.yml 的 `marketplace` job）会跑同一组测试与 lint。
