"""M9 真机爬取语料快照（每来源首页块节选，5 条）。

由 scripts/crawl_helpcenter.py 的入库结果导出：真实 Shopify/Amazon 帮助中心政策
文本的结构化切块（Amazon 404 错误页与超短块已在清洗时剔除）。长文本在源码层
折行拼接（相邻字面量隐式连接），内容与入库原文逐字符一致。放进评估夹具是为了
让 RAG 评测在 CI 里 hermetic 复现「种子五类 + 真实网页语料」的混合检索场景——
评测永不直连 PostgreSQL。
"""

WEB_CORPUS = [
    {
        "ref": "WEB-AMAZON_PRICING-01",
        "category": "platform_rule",
        "title": "Standard selling fees",
        "content":
            "Standard selling fees\nOur standard selling fees provide you with access to a pac"
            "kage of Amazon tools and programs. They’re divided into two basic types: selling"
            " plan fees and referral fees. In addition to selling fees, you might have added "
            "costs if you use certain optional tools and programs like Fulfillment by Amazon "
            "(FBA) or Amazon Ads. Learn more about selling fees Selling plans Referral fees R"
            "evenue Calculator",
        "meta": {"source": "webcrawl", "source_url": "https://sell.amazon.com/pricing"},
    },
    {
        "ref": "WEB-SHOPIFY_CONVERSIONS-01",
        "category": "platform_rule",
        "title": "Analyzing the success of your online marketing campaigns",
        "content":
            "Analyzing the success of your online marketing campaigns\nOnline advertising cost"
            "s money. To make sure you're getting the most for your advertising spend, it's a"
            " good idea to analyze the success of your marketing campaigns. Depending on your"
            " marketing goals, you might want to track your campaigns in the following ways:\n"
            "Evaluate marketing channels based on key performance metrics.\nView conversion re"
            "ports for marketing activities created in Shopify and marketing apps.\nUnderstand"
            " a customer's activities by reviewing conversion data using different attributio"
            "n models available in Shopify.\nView sessions, conversion rate, average order val"
            "ue (AOV) and other key metrics in your marketing summary.\nTrack common customer "
            "actions from an online store page using a conversion tag or Meta pixel.",
        "meta": {"source": "webcrawl", "source_url": "https://help.shopify.com/en/manual/promoting-marketing/analyze-marketing/measurement-track-conversions"},
    },
    {
        "ref": "WEB-SHOPIFY_FULFILLMENT_SERVICES-01",
        "category": "platform_rule",
        "title": "Setting up shipping and order fulfillment",
        "content":
            "Setting up shipping and order fulfillment\nYou can configure your shipping and fu"
            "lfillment settings from the Shipping and delivery page in your Shopify admin. Th"
            "ese settings determine how shipping costs are calculated, what delivery options "
            "are available to customers at checkout, and which locations fulfill orders.",
        "meta": {"source": "webcrawl", "source_url": "https://help.shopify.com/en/manual/fulfillment/setup/fulfillment-services"},
    },
    {
        "ref": "WEB-SHOPIFY_REFUNDS-01",
        "category": "policy",
        "title": "Order management and fulfillment",
        "content":
            "Order management and fulfillment\nOrder fulfillment is the workflow of processing"
            ", managing, and shipping customer orders from your Shopify store. This involves "
            "setting up your shipping configuration, managing incoming orders, and getting pa"
            "ckages to customers.",
        "meta": {"source": "webcrawl", "source_url": "https://help.shopify.com/en/manual/orders/refunds"},
    },
    {
        "ref": "WEB-SHOPIFY_REFUND_RULES-01",
        "category": "policy",
        "title": "Shopify Checkout",
        "content":
            "Shopify Checkout\nYou can use the secure Shopify Checkout to accept orders and ta"
            "ke payments wherever you sell online. After a customer adds products to a cart, "
            "they use your checkout to enter their shipping information and payment details, "
            "and then place their order. Customers can also review your store policies from y"
            "our checkout.\nYou can view and change your checkout settings, including how you "
            "want to collect customer email addresses for promotional marketing, from the Che"
            "ckout settings page in your Shopify admin. If you have an online store, then you"
            " can also change the appearance and layout of the checkout pages by editing your"
            " online store theme.",
        "meta": {"source": "webcrawl", "source_url": "https://help.shopify.com/en/manual/checkout-settings/refund-rules"},
    },
]
