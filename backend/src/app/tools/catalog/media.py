"""图片素材 brief 工具目录（M3）：平台 ImageSpec 规则 → 结构化拍摄/生图指引。

v1.4 §1.1 的 Phase 2 接缝：MVP 只产出文字 brief（真实出图接 SDXL/即梦 API 时
按同 schema 扩展 output，调用方不感知）。规则一律取自 adapter，不硬编码。
"""

from pydantic import BaseModel, Field

from app.adapters import get_adapter
from app.domain.enums import RiskLevel
from app.tools.context import ToolContext
from app.tools.registry import ToolDefinition, register


class GenerateImageBriefInput(BaseModel):
    marketplace: str = Field(min_length=1)
    product_idea: str = Field(min_length=1)
    listing_title: str = Field(default="", max_length=500)


class GenerateImageBriefOutput(BaseModel):
    marketplace: str
    main_image_count: int
    main_background: str
    allow_watermark: bool
    shot_list: list[str]
    compliance_notes: list[str]


async def _generate_image_brief(inp: GenerateImageBriefInput, ctx: ToolContext) -> dict:
    spec = get_adapter(inp.marketplace).get_rules().image_spec
    bg = "纯白背景" if spec.main_background == "white" else "场景化/自由背景"
    shots = [
        f"主图×{spec.main_count}：{bg}，产品展开态 45° 角，占画面 85%",
        "场景图：床底推入使用场景（低机位）",
        "信息图：尺寸对比与承重标注（含英文字标）",
    ]
    notes = ["不得出现水印与促销角标"] if not spec.allow_watermark else []
    notes.append("文字元素使用目标市场语言")
    return {
        "marketplace": inp.marketplace,
        "main_image_count": spec.main_count,
        "main_background": spec.main_background,
        "allow_watermark": spec.allow_watermark,
        "shot_list": shots,
        "compliance_notes": notes,
    }


register(
    ToolDefinition(
        name="generate_image_brief",
        description="按平台 ImageSpec 生成结构化拍摄/生图 brief（主图规范+分镜清单+合规注意）",
        input_model=GenerateImageBriefInput,
        output_model=GenerateImageBriefOutput,
        risk_level=RiskLevel.low,
        timeout_s=5.0,
        handler=_generate_image_brief,
    )
)
