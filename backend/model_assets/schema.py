"""Unified Digital Twin Model Asset Schema (decoupled from 3D files and line instances)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MODEL_ASSET_SCHEMA_VERSION = "1.0.0"

AssetCategory = Literal[
    "feeding", "cleaning", "adhesive", "stacking", "assembly",
    "welding", "inspection", "transport", "robot", "auxiliary",
]
InterfaceType = Literal[
    "material_flow", "mechanical", "electrical", "pneumatic", "information", "control",
]
InterfaceDirection = Literal["input", "output", "bidirectional"]
BindingStatus = Literal["unbound", "reserved", "bound"]
ModelFileFormat = Literal["glb", "gltf", "step", "stp", "usd", "obj", "fbx", "none"]
DataSourceKind = Literal["synthetic", "vendor_spec", "calibrated", "imported"]


class AssetRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class InstantiationPolicy(AssetRecord):
    """Explicit separation between reusable ModelAsset definition and line ModelInstance."""
    reusable: bool = Field(default=True, description="是否允许在同一产线中被多次实例化")
    max_parallel_instances: int | None = Field(default=None, ge=1, description="建议的最大并联实例数（为空表示不限）")
    instance_id_pattern: str = Field(default="{asset_id}#inst-{seq}", min_length=1, description="下游产线实例化时的命名模板")
    notes: str | None = None


class AssetIdentity(AssetRecord):
    asset_id: str = Field(
        min_length=2,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,63}$",
        description="稳定的模型资产唯一标识，用于与三维文件及下游实例绑定",
    )
    name: str = Field(min_length=1, max_length=160, description="资产名称")
    category: AssetCategory = Field(description="受控设备/资产类别编码")
    subcategory: str | None = Field(default=None, description="细分类型")
    description: str = Field(min_length=1, description="资产功能与适用场景描述")
    version: str = Field(default="1.0.0", min_length=1, description="资产定义版本")
    tags: list[str] = Field(default_factory=list, description="检索辅助标签")


class ResourceCapabilitySpec(AssetRecord):
    code: str = Field(min_length=1, description="与 domain_semantics 共享的标准制造能力编码")
    name: str = Field(min_length=1, description="能力中文/标准名称")
    description: str = Field(min_length=1, description="该资产实现此能力的具体工艺方式与边界")
    qualitative_features: list[str] = Field(
        default_factory=list,
        description="支持的定性工艺质量维度（复用 manufacturing.yaml quality_parameters 编码）",
    )
    is_primary: bool = Field(default=True, description="是否为核心主能力")


class MaterialSpecification(AssetRecord):
    material_type: str = Field(min_length=1, description="标准物料类型编码")
    spec_name: str = Field(min_length=1, description="规格项名称，如长度、厚度、重量")
    min_value: float | None = None
    max_value: float | None = None
    unit: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def check_bounds(self):
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError("物料规格下限不能大于上限")
        return self


class StateTransition(AssetRecord):
    material_type: str | None = Field(default=None, description="适用物料类型编码（为空表示适用于全部适配物料）")
    from_state: str = Field(min_length=1, description="输入物料状态编码")
    to_state: str = Field(min_length=1, description="输出物料状态编码")
    condition: str | None = Field(default=None, description="状态转换条件（如合格/不合格判定）")


class MaterialCompatibility(AssetRecord):
    supported_material_types: list[str] = Field(default_factory=list, description="适配的标准物料类型编码列表")
    input_material_types: list[str] = Field(default_factory=list, description="可接收的输入物料类型（为空时默认同 supported_material_types）")
    output_material_types: list[str] = Field(default_factory=list, description="可产出的输出物料类型（为空时默认同 supported_material_types）")
    specifications: list[MaterialSpecification] = Field(default_factory=list, description="物料尺寸/规格适配范围")
    input_states: list[str] = Field(default_factory=list, description="可接收的标准物料输入状态编码")
    output_states: list[str] = Field(default_factory=list, description="可产出的标准物料输出状态编码")
    state_transitions: list[StateTransition] = Field(default_factory=list, description="明确支持的物料状态转换规则")
    notes: str | None = None


class TechnicalParameter(AssetRecord):
    parameter_code: str = Field(min_length=1, description="与 domain_semantics 共享的标准技术参数编码")
    name: str = Field(min_length=1, description="参数名称")
    value: float | None = Field(default=None, description="标称值")
    lower: float | None = Field(default=None, description="可调/适用范围下限")
    upper: float | None = Field(default=None, description="可调/适用范围上限")
    unit: str = Field(min_length=1, description="标准计量单位")
    basis: str | None = Field(default=None, description="计量基准，如 电芯、单体组件、模组、件")
    capability_code: str | None = Field(default=None, description="所属能力编码（为空表示资产级通用参数）")
    note: str | None = None

    @model_validator(mode="after")
    def check_numeric_shape(self):
        if self.value is None and self.lower is None and self.upper is None:
            raise ValueError(f"技术参数 {self.parameter_code} 必须提供 value 或 lower/upper 范围")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError(f"技术参数 {self.parameter_code} 的下限大于上限")
        if self.parameter_code in {"process_time", "wait_time", "buffer_time", "takt_time", "payload", "laser_power", "force"}:
            for num in (self.value, self.lower, self.upper):
                if num is not None and num < 0:
                    raise ValueError(f"技术参数 {self.parameter_code} 不能为负数")
        return self


class AssetInterface(AssetRecord):
    interface_id: str = Field(min_length=1, description="接口局部唯一标识")
    name: str = Field(min_length=1, description="接口名称")
    interface_type: InterfaceType = Field(description="接口类型")
    direction: InterfaceDirection = Field(description="接口方向")
    compatible_conditions: list[str] = Field(default_factory=list, description="兼容条件或信息载荷类型（如 识别信息、标识信息、工装板规格）")
    protocol: str | None = Field(default=None, description="通信或机械连接协议，如 OPC-UA、PROFINET、ISO-9409-1")
    payload_schema: str | None = Field(default=None, description="信息接口的数据结构说明")
    optional: bool = Field(default=False, description="是否为可选扩展接口")


class Dimensions3D(AssetRecord):
    length: float = Field(gt=0, description="长度 (mm)")
    width: float = Field(gt=0, description="宽度 (mm)")
    height: float = Field(gt=0, description="高度 (mm)")


class ConnectionPoint(AssetRecord):
    point_id: str = Field(min_length=1)
    interface_id: str | None = Field(default=None, description="关联的接口 ID")
    position_mm: tuple[float, float, float] = Field(description="在局部坐标系中的位置 [x, y, z] (mm)")
    normal: tuple[float, float, float] | None = Field(default=None, description="朝向法向量 [nx, ny, nz]")


class GeometryInfo(AssetRecord):
    dimensions_mm: Dimensions3D | None = Field(default=None, description="包络尺寸 (mm)，暂无真实三维模型时允许为空")
    coordinate_system: str | None = Field(default="RHS_Z_UP_MM", description="局部坐标系约定")
    mounting_datum: str | None = Field(default=None, description="安装基准说明")
    connection_points: list[ConnectionPoint] = Field(default_factory=list, description="物理连接点坐标")
    completeness: Literal["complete", "partial", "placeholder"] = Field(default="partial")


class BehaviorModel(AssetRecord):
    cycle_time_s: float | None = Field(default=None, gt=0, description="标称单次加工/运行时间 (s)")
    cycle_time_basis: str | None = Field(default=None, description="加工时间计量基准，如 电芯、单体组件、模组")
    batch_size: int | None = Field(default=None, ge=1, description="单次批处理数量")
    operating_states: list[str] = Field(
        default_factory=lambda: ["IDLE", "RUNNING", "FAULT", "MAINTENANCE"],
        description="标准运行状态集合",
    )
    behavior_model_ref: str | None = Field(default=None, description="行为仿真模型引用（如 FMU/状态机路径，允许为空）")
    behavior_model_type: Literal["fmu", "state_machine", "discrete_event", "analytical", "none"] | None = None
    availability: float | None = Field(default=None, gt=0, le=1, description="标称可用率 (0~1)")


class ModelBinding(AssetRecord):
    model_uri: str | None = Field(default=None, description="三维模型文件 URI 或相对路径（如 geometry/MA-FEED-01.glb），允许为空")
    file_format: ModelFileFormat | None = Field(default=None, description="三维模型文件格式")
    binding_status: BindingStatus = Field(default="unbound", description="绑定状态：unbound / reserved / bound")
    asset_ref_key: str | None = Field(default=None, description="用于后续模型文件解耦绑定的稳定键值（默认同 asset_id）")
    checksum_sha256: str | None = Field(default=None, description="绑定三维模型文件校验和")
    notes: str | None = None

    @model_validator(mode="after")
    def check_binding_consistency(self):
        if self.binding_status == "bound":
            if not (self.model_uri or "").strip():
                raise ValueError("binding_status 为 bound 时必须提供 model_uri")
            if not self.file_format or self.file_format == "none":
                raise ValueError("binding_status 为 bound 时必须提供有效的三维文件格式")
        return self


class AssetProvenance(AssetRecord):
    author: str = Field(default="synthetic_catalog_builder")
    created_at: str = Field(default="2026-11-24T00:00:00Z")
    synthetic_disclaimer: str | None = Field(
        default="Synthetic experimental model asset for Assessment resource-matching benchmarks; not real vendor equipment data."
    )
    notes: str | None = None


class ModelAsset(AssetRecord):
    """Unified Digital Twin Model Asset Definition (8 core semantic modules)."""
    schema_version: Literal["1.0.0"] = MODEL_ASSET_SCHEMA_VERSION
    data_source: DataSourceKind = Field(default="synthetic", description="资产数据来源标注")
    instantiation_policy: InstantiationPolicy = Field(default_factory=InstantiationPolicy)
    identity: AssetIdentity
    capabilities: list[ResourceCapabilitySpec] = Field(min_length=1, description="标准制造能力列表")
    material_compatibility: MaterialCompatibility = Field(default_factory=MaterialCompatibility)
    parameters: list[TechnicalParameter] = Field(default_factory=list, description="技术参数列表")
    interfaces: list[AssetInterface] = Field(default_factory=list, description="连接接口列表")
    geometry: GeometryInfo | None = Field(default=None, description="几何与安装基准信息（允许为空或部分缺失）")
    behavior: BehaviorModel | None = Field(default=None, description="行为与节拍模型信息（允许为空）")
    model_binding: ModelBinding = Field(default_factory=ModelBinding, description="三维模型解耦绑定信息")
    provenance: AssetProvenance = Field(default_factory=AssetProvenance)

    @model_validator(mode="after")
    def check_asset_integrity(self):
        if self.data_source == "synthetic" and not (self.provenance.synthetic_disclaimer or "").strip():
            raise ValueError("synthetic 实验资产必须明确填写 provenance.synthetic_disclaimer 说明")
        if self.model_binding.asset_ref_key is None:
            self.model_binding.asset_ref_key = self.identity.asset_id
        cap_codes = [c.code for c in self.capabilities]
        if len(cap_codes) != len(set(cap_codes)):
            raise ValueError(f"资产 {self.identity.asset_id} 存在重复的制造能力编码")
        iface_ids = [iface.interface_id for iface in self.interfaces]
        if len(iface_ids) != len(set(iface_ids)):
            raise ValueError(f"资产 {self.identity.asset_id} 存在重复的接口 ID")
        if self.geometry:
            known_ifaces = set(iface_ids)
            for pt in self.geometry.connection_points:
                if pt.interface_id and pt.interface_id not in known_ifaces:
                    raise ValueError(f"几何连接点 {pt.point_id} 引用了不存在的接口 {pt.interface_id}")
        known_caps = set(cap_codes)
        for param in self.parameters:
            if param.capability_code and param.capability_code not in known_caps:
                raise ValueError(f"技术参数 {param.parameter_code} 引用了未声明的能力编码 {param.capability_code}")
        return self

    @property
    def asset_id(self) -> str:
        return self.identity.asset_id

    @property
    def capability_codes(self) -> list[str]:
        return [c.code for c in self.capabilities]
