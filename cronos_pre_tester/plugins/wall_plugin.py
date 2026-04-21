"""
拼接墙操作插件：
- 查询信号源（80400/80401）
- 查询屏组（80800/80801）
- 开窗（80826/80827）
- 关窗（80828/80829）
- 查询屏组所有窗口（80822/80823）
- 关闭所有窗口（80824/80825）
- 修改窗口参数（80832/80833）
"""
import xml.etree.ElementTree as ET
from .base import ProtocolPlugin, FieldExtractor
from . import register_plugin


# =============================================================================
# 信号源查询
# =============================================================================
@register_plugin(80400)
class SignalSourcePlugin(ProtocolPlugin):
    name = "查询信号源"
    category = "拼接"

    request_proto = 80400
    response_proto = 80401

    request_xml_template = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<Message>{user_id_xml}</Message>"
    )

    def parse_response(self, xml_body: str) -> dict:
        """解析所有信号源，返回列表。"""
        result = {"_type": "signal_sources", "sources": []}
        try:
            root = ET.fromstring(xml_body)
            # 物理端口
            for phy in root.findall(".//PhyPort"):
                pid = phy.get("id", "")
                ptype = phy.get("type", "")
                valid = phy.get("valid", "")
                w = phy.get("w", "")
                h = phy.get("h", "")
                rate = phy.get("rate", "")
                for lp in phy.findall("LogicalPort"):
                    sources = result["sources"]
                    sources.append({
                        "id": lp.get("id", ""),
                        "name": lp.get("name", ""),
                        "phyPort": pid,
                        "phyChn": lp.get("phychn", ""),
                        "valid": lp.get("valid", valid),
                        "w": lp.get("w", w),
                        "h": lp.get("h", h),
                        "rate": lp.get("rate", rate),
                        "type": "physical",
                    })
            # 虚拟端口
            for vir in root.findall(".//VirPort"):
                vid = vir.get("id", "")
                vname = vir.get("name", "")
                hnum = vir.get("hnum", "1")
                vnum = vir.get("vnum", "1")
                vir_w = vir.get("w", "")
                vir_h = vir.get("h", "")
                for port in vir.findall("Port"):
                    sources = result["sources"]
                    sources.append({
                        "id": port.get("input", ""),
                        "name": f"{vname}-端口{port.get('id','')}",
                        "phyPort": vid,
                        "phyChn": "0",
                        "valid": "1",
                        "w": vir_w,
                        "h": vir_h,
                        "rate": "",
                        "type": "virtual",
                        "hnum": hnum,
                        "vnum": vnum,
                    })
        except ET.ParseError:
            result["_raw"] = xml_body
        return result


# =============================================================================
# 屏组信息查询
# =============================================================================
@register_plugin(80800)
class ScreenGroupPlugin(ProtocolPlugin):
    name = "查询屏组"
    category = "拼接"

    request_proto = 80800
    response_proto = 80801

    request_xml_template = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<Message>{user_id_xml}"
        "<Screens type=\"all\"/>"
        "</Message>"
    )

    def parse_response(self, xml_body: str) -> dict:
        """解析所有屏组，返回列表。"""
        result = {"_type": "screen_groups", "groups": []}
        try:
            root = ET.fromstring(xml_body)
            for screen in root.findall(".//Screen"):
                info = {
                    "id": screen.get("id", ""),
                    "mode": "",
                    "name": "",
                    "hnum": "0",
                    "vnum": "0",
                    "hgap": "0",
                    "vgap": "0",
                    # display
                    "dis_act_hsize": "",
                    "dis_act_vsize": "",
                    "dis_freq": "",
                    "dis_hpix_mode": "",
                }
                smod = screen.find("smod")
                if smod is not None:
                    info["hnum"] = (smod.findtext("hnum") or "").strip()
                    info["vnum"] = (smod.findtext("vnum") or "").strip()
                    info["hgap"] = (smod.findtext("hgap") or "").strip()
                    info["vgap"] = (smod.findtext("vgap") or "").strip()

                display = screen.find("display")
                if display is not None:
                    info["dis_act_hsize"] = (display.findtext("dis_act_hsize") or "").strip()
                    info["dis_act_vsize"] = (display.findtext("dis_act_vsize") or "").strip()
                    info["dis_freq"] = (display.findtext("dis_freq") or "").strip()
                    info["dis_hpix_mode"] = (display.findtext("dis_hpix_mode") or "").strip()

                name_elem = screen.find("name")
                if name_elem is not None and name_elem.text:
                    info["name"] = name_elem.text.strip()
                mode_elem = screen.find("mode")
                if mode_elem is not None and mode_elem.text:
                    info["mode"] = mode_elem.text.strip()

                # 计算总分辨率
                hnum = int(info["hnum"]) if info["hnum"].isdigit() else 0
                vnum = int(info["vnum"]) if info["vnum"].isdigit() else 0
                act_h = int(info["dis_act_hsize"]) if info["dis_act_hsize"].isdigit() else 0
                act_v = int(info["dis_act_vsize"]) if info["dis_act_vsize"].isdigit() else 0
                info["total_w"] = hnum * act_h
                info["total_h"] = vnum * act_v

                result["groups"].append(info)
        except ET.ParseError:
            result["_raw"] = xml_body
        return result


# =============================================================================
# 开窗
# =============================================================================
@register_plugin(80826)
class OpenWindowPlugin(ProtocolPlugin):
    name = "开窗"
    category = "拼接"

    request_proto = 80826
    response_proto = 80827

    request_xml_template = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<Message>{user_id_xml}"
        "<Screen id=\"{screen_id}\">"
        "<Win x0=\"{x0}\" y0=\"{y0}\" x1=\"{x1}\" y1=\"{y1}\" bcolor=\"{bcolor}\" bwidth=\"{bwidth}\">"
        "<Src id=\"{src_id}\"/>"
        "</Win>"
        "</Screen>"
        "</Message>"
    )

    response_xpaths = [
        FieldExtractor("screen_id", ".//Screen", "id"),
        FieldExtractor("win_id", ".//Win", "id"),
    ]


# =============================================================================
# 关窗
# =============================================================================
@register_plugin(80828)
class CloseWindowPlugin(ProtocolPlugin):
    name = "关窗"
    category = "拼接"

    request_proto = 80828
    response_proto = 80829

    request_xml_template = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<Message>{user_id_xml}"
        "<Screen id=\"{screen_id}\">"
        "<Win id=\"{win_id}\"/>"
        "</Screen>"
        "</Message>"
    )


# =============================================================================
# 查询屏组所有窗口
# =============================================================================
@register_plugin(80822)
class QueryWindowsPlugin(ProtocolPlugin):
    name = "查询窗口"
    category = "拼接"

    request_proto = 80822
    response_proto = 80823

    request_xml_template = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<Message>{user_id_xml}"
        "<Screens>"
        "<Screen id=\"{screen_id}\"/>"
        "</Screens>"
        "</Message>"
    )

    def parse_response(self, xml_body: str) -> dict:
        result = {"_type": "windows", "screen_id": "", "windows": []}
        try:
            root = ET.fromstring(xml_body)
            screen = root.find(".//Screen")
            if screen is not None:
                result["screen_id"] = screen.get("id", "")
                for win in screen.findall("Win"):
                    info = {
                        "id": win.get("id", ""),
                        "x0": win.get("x0", ""),
                        "y0": win.get("y0", ""),
                        "x1": win.get("x1", ""),
                        "y1": win.get("y1", ""),
                        "bcolor": win.get("bcolor", ""),
                        "bwidth": win.get("bwidth", ""),
                        "src_id": "",
                        "phyPort": "",
                        "subChn": "",
                    }
                    src = win.find("Src")
                    if src is not None:
                        info["src_id"] = src.get("id", "")
                        info["phyPort"] = src.get("phyPort", "")
                        info["subChn"] = src.get("subChn", "")
                    result["windows"].append(info)
        except ET.ParseError:
            result["_raw"] = xml_body
        return result


# =============================================================================
# 关闭所有窗口
# =============================================================================
@register_plugin(80824)
class CloseAllWindowsPlugin(ProtocolPlugin):
    name = "关闭所有窗口"
    category = "拼接"

    request_proto = 80824
    response_proto = 80825

    request_xml_template = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<Message>{user_id_xml}"
        "<Screens>"
        "<Screen id=\"{screen_id}\"/>"
        "</Screens>"
        "</Message>"
    )


# =============================================================================
# 修改窗口参数
# =============================================================================
@register_plugin(80832)
class ModifyWindowPlugin(ProtocolPlugin):
    name = "修改窗口"
    category = "拼接"

    request_proto = 80832
    response_proto = 80833

    request_xml_template = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<Message>{user_id_xml}"
        "<Screen id=\"{screen_id}\">"
        "<Win id=\"{win_id}\" x0=\"{x0}\" y0=\"{y0}\" x1=\"{x1}\" y1=\"{y1}\" bcolor=\"{bcolor}\" bwidth=\"{bwidth}\">"
        "<Src id=\"{src_id}\"/>"
        "</Win>"
        "</Screen>"
        "</Message>"
    )


# =============================================================================
# 底图列表查询 (80842/80843)
# =============================================================================
@register_plugin(80842)
class BkListPlugin(ProtocolPlugin):
    """内部使用，底图列表查询。"""
    name = "查询底图列表"
    category = "拼接"

    request_proto = 80842
    response_proto = 80843

    request_xml_template = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<Message>{user_id_xml}</Message>"
    )

    def parse_response(self, xml_body: str) -> dict:
        result = {"_type": "backdrops", "backdrops": [], "free_space": ""}
        try:
            root = ET.fromstring(xml_body)
            bklist = root.find(".//BkList")
            if bklist is not None:
                result["free_space"] = bklist.get("freeSpace", "")
                for bk in bklist.findall("Bk"):
                    result["backdrops"].append({
                        "id": bk.get("id", ""),
                        "name": bk.get("name", ""),
                        "w": bk.get("w", ""),
                        "h": bk.get("h", ""),
                    })
        except ET.ParseError:
            result["_raw"] = xml_body
        return result


# =============================================================================
# 底图设置 (80850/80851)
# =============================================================================
@register_plugin(80850)
class BkSetPlugin(ProtocolPlugin):
    name = "设置底图"
    category = "拼接"

    request_proto = 80850
    response_proto = 80851

    request_xml_template = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<Message>{user_id_xml}"
        "<Screen id=\"{screen_id}\">"
        "<Enable>{enable}</Enable>"
        "<Bk id=\"{bk_id}\" x=\"{x}\" y=\"{y}\" w=\"{w}\" h=\"{h}\"/>"
        "</Screen>"
        "</Message>"
    )


# =============================================================================
# OSD 滚动字幕查询 (80874/80875)
# =============================================================================
@register_plugin(80874)
class OsdMoveQueryPlugin(ProtocolPlugin):
    """查询屏组滚动字幕（Move）列表。"""
    name = "查询滚动字幕"
    category = "拼接"

    request_proto = 80874
    response_proto = 80875

    request_xml_template = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<Message>{user_id_xml}"
        "<Screens>"
        "<Screen id=\"{screen_id}\"/>"
        "</Screens>"
        "</Message>"
    )

    def parse_response(self, xml_body: str) -> dict:
        """
        解析滚动字幕列表响应。
        返回 {"screens": [{"id": "4", "select": "40001",
                          "res_w": 1920, "res_h": 1080,
                          "moves": [{"id": "40001", "name": "...", ...}, ...]}, ...]}
        """
        result = {"_type": "osd_moves", "screens": []}
        try:
            root = ET.fromstring(xml_body)
            for screen in root.findall(".//Screen"):
                sid = screen.get("id", "")
                select = screen.get("select", "")
                res = screen.find("Resolution")
                res_w = int(res.get("w", 1920)) if res is not None else 1920
                res_h = int(res.get("h", 1080)) if res is not None else 1080
                moves = []
                for mv in screen.findall("Move"):
                    moves.append({
                        "id": mv.get("id", ""),
                        "name": mv.get("name", ""),
                        "type": mv.get("type", ""),
                        "start_row": mv.findtext("StartRow", ""),
                        "start_col": mv.findtext("StartCol", ""),
                        "direction": mv.findtext("Direction", ""),
                        "mov_step": mv.findtext("MovStep", ""),
                        "hstep": mv.findtext("MoveHstep", ""),
                        "vstep": mv.findtext("MoveVstep", ""),
                        "frame_count": mv.findtext("FrameCount", ""),
                        "img_w": mv.find("Img").get("w", "") if mv.find("Img") is not None else "",
                        "img_h": mv.find("Img").get("h", "") if mv.find("Img") is not None else "",
                    })
                result["screens"].append({
                    "id": sid,
                    "select": select,
                    "res_w": res_w,
                    "res_h": res_h,
                    "moves": moves,
                })
        except ET.ParseError:
            result["_raw"] = xml_body
        return result


# =============================================================================
# OSD 滚动字幕参数设置 (80862/80863)
# =============================================================================
@register_plugin(80862)
class OsdMoveSetPlugin(ProtocolPlugin):
    """设置屏组滚动字幕参数（使能/禁能字幕）。"""
    name = "设置滚动字幕"
    category = "拼接"

    request_proto = 80862
    response_proto = 80863

    request_xml_template = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<Message>{user_id_xml}"
        "<Screen id=\"{screen_id}\">"
        "<Enable>1</Enable>"
        "<MoveEnable>1</MoveEnable>"
        "<Move id=\"{move_id}\">"
        "<StartRow>{start_row}</StartRow>"
        "<StartCol>{start_col}</StartCol>"
        "<Direction>{direction}</Direction>"
        "<MovStep>{mov_step}</MovStep>"
        "<FrameCount>{frame_count}</FrameCount>"
        "</Move>"
        "</Screen>"
        "</Message>"
    )
