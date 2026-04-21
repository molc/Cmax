from abc import ABC, abstractmethod
import xml.etree.ElementTree as ET
from dataclasses import dataclass


_XML_ESCAPE_TABLE = str.maketrans({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&apos;",
})


def _xml_escape(s: str) -> str:
    """将字符串转义为合法的 XML 文本内容。"""
    return str(s).translate(_XML_ESCAPE_TABLE)


@dataclass
class FieldExtractor:
    name: str
    xpath: str
    attr: str = ""   # XML属性名，为空则取 text

    def extract(self, root: ET.Element) -> str:
        elem = root.find(self.xpath)
        if elem is None:
            return ""
        return elem.get(self.attr) if self.attr else (elem.text or "").strip()


class ProtocolPlugin(ABC):
    """插件基类，定义协议操作的通用接口。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """插件名称，显示在 UI 中。"""

    @property
    @abstractmethod
    def category(self) -> str:
        """所属类别。"""

    @property
    @abstractmethod
    def request_proto(self) -> int:
        """请求协议号。"""

    @property
    def response_proto(self) -> int:
        """响应协议号，默认为请求号+1。"""
        return self.request_proto + 1

    @property
    @abstractmethod
    def request_xml_template(self) -> str:
        """请求 XML 模板（含占位符）。"""

    @property
    def response_xpaths(self) -> list[FieldExtractor]:
        """从响应 XML 中提取的关键字段列表。"""
        return []

    def build_request_xml(self, session, **kwargs) -> str:
        """
        填充模板，返回完整的请求 XML 字符串。
        默认实现做简单的占位符替换，并对占位符值进行 XML 转义。
        """
        xml = self.request_xml_template
        # 替换 session 占位符
        xml = xml.replace("{user_id}", str(session.user_id))
        xml = xml.replace("{user_id_xml}", session.user_id_xml())
        # 替换其他占位符（对用户输入进行 XML 转义，防止注入）
        for key, val in kwargs.items():
            xml = xml.replace(f"{{{key}}}", _xml_escape(str(val)))
        return xml

    def parse_response(self, xml_body: str) -> dict:
        """解析响应 XML，返回字段名->值的字典。默认实现使用 xpaths。"""
        result = {}
        try:
            root = ET.fromstring(xml_body)
            for fe in self.response_xpaths:
                result[fe.name] = fe.extract(root)
        except ET.ParseError:
            result["_raw"] = xml_body
        return result
