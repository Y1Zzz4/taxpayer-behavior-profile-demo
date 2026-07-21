# # nohup python rolling_llm_summary.py > summary_run.log 2>&1 &
# USE_YESTERDAY_ALERT = False   # True：启用昨日预警逻辑；False：完全关闭
# import pandas as pd
# from openai import OpenAI
# from tqdm import tqdm
# import re
# import json
# import os

# config_path = r"/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/config.json"
# with open(config_path, 'r') as f:
#     config = json.load(f)

# today = config["today"]         # 202XXXXX
# date_time = config["date_time"] # 202X-XX-XX
# date = config["date"]           # XX月XX日

# DEBUG_MODE = False  # 设置为 True 时，不调用大模型，返回空字符串
# client = OpenAI(base_url="http://127.0.0.1:9997/v1", api_key="None")

# # === 工具：分割编号问题（1. ... 2. ...） ===
# def split_by_numbered_blocks(text):
#     parts = re.split(r"(?m)^(?=\d+\.\s*)", text.strip())
#     return [p.strip() for p in parts if p.strip()]

# # === 提炼核心反映问题 ===
# def extract_core_issue(numbered, model='qwen2.5-instruct-int4'):
#     if DEBUG_MODE:
#         return ""
#     template = f"""
#    你是一个税务业务专家。你的任务是：（1）阅读纳税人的每条来电记录并识别核心诉求；（2）整理归纳纳税人的共性诉求，提炼出一个核心问题；（3）判断该核心问题出现的频次；（4）判断来电人反映事项的涉及区域。

# 请阅读下列多条来电问题，提炼出唯一一条反映纳税人同类诉求的的核心问题，并满足以下全部要求：
# 【核心问题输出要求】
#     - 只输出一句话，输出结果以问句形式呈现；
#     - 必须体现逻辑连通性：用“因-果”或“场景-诉求”结构，让读者一眼看出“问题发生的场景 + 纳税人真正想解决的核心诉求”；
#     - 禁止出现并列连词（如“及”“和”“与”），禁止罗列多条问题；
#     - 避免空泛词（“相关问题”“如何操作”），必须给出确切场景 + 关键动作；
# -- 提炼核心问题时要正确理解税务相关知识间的因果联系，避免类似答案：“个税记录更正及补缴公积金”，实际应为“因公积金补缴如何更正个人所得税汇算清缴？”。
# -- 提炼的核心问题应当简洁准确，突出核心问题，应既没有个性化信息，又能提炼纳税人具体咨询内容。
# -- 核心问题正确输出样例：“企业税款已缴纳但仍提示欠费，怎么处理？”，“查询历史工单处理进度”，“税控设备被锁定如何完成解锁？”，“公司给员工发放的生育津贴补差部分是否可以享受个人所得税免税政策？”，“税费种认定调整失败是什么原因导致的？如何解决？”，“外国人未在规定时间内进行居民纳税申报是否会涉及滞纳金？如何更正外籍人员的免税项目申报？”，“在上海有外建项目如果没有收到预收款需要预缴增值税和企业所得税吗？”，等等。
# -- 只能提炼一个核心问题，上面的样例只是告诉你核心问题大致长啥样，你需要找到问题组中最重要、最能反映纳税人共性诉求的问题。
# -- 提炼的核心问题颗粒度应细一点，避免把相关性不大的问题归纳为同一问题，避免问题杂糅、颗粒度大。
# -- 提炼的核心问题应明确具体，避免将多个问题简单拼接在一起，造成问题杂糅、颗粒度大。
# -- 【注意】可以根据实际情况输出1-2个问句。如果输出2个问句，那么这2个问句间必须有关联且为递进关系！！！！以下为几组核心问题命名示例：
# 示例1：“如何办理公司的税务迁移？如何处理公司迁移时未办结的税务事项？”
# 示例2：“个人如何代开劳务发票？个人代开劳务发票需要缴纳哪些税费及税率如何计算？”
# 示例3：“企业销售二手车如何申报增值税？如何申报销售二手车未开票收入的增值税？如果销售未抵扣进项税的二手车，应按什么税率申报增值税？”
# 示例4：“如何在电子税务局上操作签订或变更三方协议？如何解决三方协议验证失败的问题？”
# 示例5：“企业申请发票额度被驳回的原因是什么？如何解决因存在涉税风险被驳回的问题？”
# 示例6：“公司收到新股东实收资本后，应按实际收到的金额缴纳印花税吗？如何按次申报？”
# 示例7：“小规模纳税人如何在增值税申报表中申报3%税率的发票？如何申报未开票收入？”
# 示例8：“企业所得税申报中职工薪酬的填报口径是什么？如何区分和正确填写已计入成本费用的职工薪酬和实际支付给职工的应付职工薪酬？职工薪酬是否包含公司承担的社保和公积金？职工薪酬应填写累计数还是当季数？”
# 示例9：“跨境电商已缴纳国外销售税，国内申报时是否重复缴纳增值税?应该如何申报？”

# 【下述示例核心问题颗粒度大、问题杂糅，应避免！】
# （1）“如何在电子税务局或社保客户端调整和查询社保缴费基数及申报记录？”因为缴费基数和申报记录是两个比较大的业务，且这两个模块不在一起，因此不能将社保费基数和申报记录简单合并为一个合并为问题!
# （2）“公司进行各种交易或服务时如何确定应缴纳的税种及税率？”“企业如何享受税收优惠政策及具体操作流程是什么？”“海关缴款书相关问题如何处理？”，避免使用各种、各类、相关等宽泛的表述，颗粒度太粗了！
# （3）“如何申请提高小规模纳税人的发票额度和恢复信用等级？”问题杂糅，既涉及发票额度调整，又涉及信用，不应该合成为一个核心问题!
# （4）“如何处理红冲发票后的退税问题？”粒度太粗，这个业务一般是个人向税务机关申请代开发票后，然后红冲需要退税，可以明确一下，例如变成“如何申请个人代开发票红冲后产生的退税？”，具体情况结合实际语境细化！
# （5）“如何在电子税务局更正个人所得税申报？”，在电子税务局无法更正个人所得税申报，可能描述的是自然人电子税务局，需要注意全称，企业申报个人所得税相关业务都是在自然人电子税务局办理。
# （6）“企业与个人之间的股权转让涉及哪些税种及如何申报？”这个问题表述不明确，如果是个人把自己的股权转让给企业，应该是“个人股权转让”这个大类，如果是企业转让股权给个人，那么这个问题可以表述的更明确一点。
# （7）“如何在自然人电子税务局申报个人股权转让的印花税和个税？”总结有误，自然人电子税务局上无法申报印花税，印花税申报应该在新电子税局上完成，提炼问题时应避免简单拼接和杂糅！
# （8）“如何在电子税务局开具和下载各类完税证明？”粒度过粗，尽量明确到开具什么类别什么税种的完税证明，例如个人所得税完税证明等。
# （9）“如何联系专管员确认股权转让相关问题？”粒度过粗，该类问题既找专管员、也要解决业务问题，首先对于此类问题提炼时应分清主次，该问题的核心是解决股权转让业务问题，重点提炼业务问题核心部分；其次“股权转让相关问题”粒度过大，应明确具体什么问题。重点是对业务问题的提炼，切记！！！！


#     ### 示例1：
#     输入：
#     1. 如何解决因动迁房未注销导致的房产交易加税问题？
#     2. 如何处理因动迁导致的房产未注销而需加税的问题？
#     3. 如何处理拆迁后房产交易时的加税问题？
#     4. 房屋交易是否需要缴纳额外税款？
#     5. 如何处理动迁后未注销房产的交易涉税问题？
#     6. 如何处理房产交易中的加税问题？
#     7. 房屋交易的加税问题如何处理？
#     8. 如何将业务转到杨浦区税务机关？
#     9. 如何处理房子交易中遇到的税务问题？
#     10. 如何办理房屋交易的税务手续？
#     11. 如何处理因房屋拆迁导致的房产税问题？
#     12. 如何办理房产交易的税务登记？
#     13. 如何确认灵活就业人员的社保扣款是否成功？
#     14. 如何处理房屋已不存在但系统仍显示需缴纳税款的问题？
#     返回：
#     如何解决动迁房未注销导致的房产交易加税问题？

#     ### 示例2：
#     输入：
#     1. 如何申报境外利息收入的个人所得税？如何计算境外利息收入的个人所得税？
#     2. 如何处理个税补申报时因已申报月份数据导致无法申报的问题？
#     3. 如何在个税APP上补申报境外所得？
#     4. 如何在个税APP申报境外利息收入？
#     5. 如何在自然人电子税务局申报境外收入的个人所得税？
#     6. 如何在自然人电子税务局更正境外所得的申报信息？
#     7. 如何处理个税APP中更正申报后扣除项消失导致税额增加的问题？
#     8. 如何处理更正申报后导致的个人所得税税额翻倍问题？
#     9. 如何处理更正申报后专项附加扣除信息消失的问题？
#     返回：
#     如何申报或更正境外利息收入的个人所得税？

#     ### 示例3：
#     输入：
#     1. 如何在电子税务局申请扣缴客户端的数据下载权限？
#     2. 如何在电子税务局使用企业信息登录自然人业务？
#     3. 如何解决个人所得税APP登录问题？如何处理电子税务局数据被覆盖问题？
#     4. 如何解决电子税务局人脸识别认证时显示二维码无效的问题？
#     5. 如何解决电子税务局人工服务无法使用的问题？
#     6. 如何解决电子税务局登录后无法找到企业账户中心的问题？
#     7. 如何在电子税务局切换身份并获取不同企业的纳税互动数据？
#     8. 如何解决电子税务局显示“该地区暂未开放人工服务”的问题？
#     返回：
#     如何解决电子税务局登录、身份切换、人工服务无法使用问题？

#     ### 示例4：
#     输入：
#     1. 境外居民如何申报缴纳股权转让的印花税？需要准备哪些材料？
#     2. 境外企业办理股权转让印花税需要准备哪些材料？如何确认办税大厅的具体要求？
#     3. 公司如何为外国受让方在电子税局上做临时税务登记并获取F码？外国受让方在电子税局上缴纳印花税时遇到支付问题，如何解决？如何在线下缴纳税款并获取缴款通知？
#     4. 受让方需要准备哪些材料来缴纳股权转让的印花税？有限合伙企业是否可以按个人名义缴纳印花税？临时税务登记的流程和所需材料是什么？如果境外企业的公章无法带入境内，如何处理？网上缴纳印花税时遇到问题，是否需要到办税大厅办理？
#     5. 如何在电子税局为境外非居民办理临时税务登记并缴纳印花税？如何在电子税局录入境外非居民的身份信息和合同信息？
#     6. 有限合伙企业是否需要缴纳印花税？临时税务登记需要准备哪些资料？境外非居民企业进行临时税务登记是否必须提供公章？
#     7. 查询专管员的联系方式？
#     8. 公司进行股权转让需要做临时税务登记吗？临时税务登记需要哪些材料？经办人办理临时税务登记和印花税业务需要提供委托授权书吗？公章无法带至现场，如何处理盖章问题？临时税务登记表是否需要提前准备？
#     9. 公司如何在电子税局上传境外非居民的电子版纳税证明？电子版纳税证明是否需要盖公章？

#     返回：
#     非居民企业股权转让如何缴纳印花税？
#     ### 当前问题列表：
#     {numbered}

#     核心问题：
#     """
#     resp = client.chat.completions.create(
#         model=model,
#         messages=[
#             {"role": "system", "content": "你是税务知识专家。"},
#             {"role": "user", "content": template}
#         ],
#         temperature=0.2
#     ).choices[0].message.content.strip()
#     return resp

# # === 判断子问题是否相关 ===
# def judge_relevance(core, single_q, model='qwen2.5-instruct-int4'):
#     if DEBUG_MODE:
#         return True
#     prompt = f"""
#     你是一个税务业务专家。已知核心问题：“{core}”。请判断下面这一条问题是否在意图上属于为了解决该核心问题而提出的（只有部分相关也可以）：
#     “{single_q}”
#     如果相关，请只返回“true”，否则只返回“false”，注意，你无需返回任何解释，只能选择true或false中的某一个作为回复。
#     若待判断问题为： "查询专管员的联系方式"，或类似问题，直接返回false。
#     ### 示例1：
#     核心问题：如何申报或更正境外利息收入的个人所得税？
#     待判断问题：如何在个税APP申报境外利息收入？

#     返回：true

#     ### 示例2：
#     核心问题：非居民企业股权转让如何缴纳印花税？
#     待判断问题： 查询专管员的联系方式？

#     返回：false

#     ### 示例3：
#     核心问题：非居民企业股权转让如何缴纳印花税？
#     待判断问题： 如何填报个税专项附加扣除信息？

#     返回：false
    
#     ### 示例4：
#     核心问题：公司扣缴个税时三方协议扣款失败如何解决？
#     待判断问题：公司通过三方协议扣缴个人所得税时，为什么会出现缴款不成功的情况？

#     返回：true

#     """
#     resp = client.chat.completions.create(
#         model=model,
#         messages=[
#             {"role": "system", "content": "你是中国的税务知识专家，擅长税务问题的总结和评判。"},
#             {"role": "user", "content": prompt}
#         ],
#         temperature=0
#     ).choices[0].message.content.strip().lower()
#     return resp == "true"

# # === 提取代表性问题 ===
# def extract_representative_issue(issue_list, label="红灯预警", model="qwen2.5-instruct-int4"):
#     if DEBUG_MODE:
#         return ""
#     if not issue_list:
#         return ""
#     combined_text = "。\n".join(issue_list)
#     prompt = f"""
#     你是一个税务领域的人工智能助手，请从下列多个纳税人反映的问题中，归纳出一条代表性最强、重复率最高、表达最清晰的问题陈述。
#     - 只需输出一句话，字数控制在40字以内。
#     - 不能出现“等”“多个问题”等模糊字眼。
#     - 必须反映出具体的涉税问题或操作困难。
#     - 不得罗列多个问题，仅保留一个代表性问题。

#     问题列表（{label}）：
#     {combined_text}

#     请输出一条代表性问题（40字以内）：
#     """
#     response = client.chat.completions.create(
#         model=model,
#         messages=[
#             {"role": "system", "content": "你是中国税务专家助手，擅长归纳问题"},
#             {"role": "user", "content": prompt}
#         ],
#         temperature=0.2
#     ).choices[0].message.content.strip()
#     return response

# # ====== 提炼优先级1地域（已重写） ======
# def tier_1_priority_area(transcript: str, priority2_hint: str, model="qwen2.5-instruct-int4"):
#     """
#     根据全部对话记录 + 已统计到的登记单位(priority2_hint)，
#     综合判断纳税人最有可能涉及的行政区划（优先级1）。
#     """
#     if DEBUG_MODE:
#         return ""
#     prompt = f"""
# 你是一个税务领域的人工智能助手。
# 请根据下列对话记录以及“已统计登记单位”信息，判断纳税人本次系列来电中最有可能涉及的行政区划（优先级1区域）。
# 要求：
# - 仅输出一个词，必须以“区”结尾，请在“黄浦区”、“徐汇区”、“静安区”、“普陀区”、“长宁区”、“虹口区”、“杨浦区”、“浦东新区”、“奉贤区”、“嘉定区”、“闵行区”、“松江区”、“金山区”、“青浦区”、“宝山区”、“崇明区”中选一个，只能选一个，且必须与我给出的区名一字不差。  
# - 如果对话中纳税人明确提到某区，则以纳税人提到的为准。  
# - 若纳税人未明确提及，则结合“已统计登记单位”与对话上下文综合判断。  
# - 如果仍无法确定，则输出空字符串，不得输出多个区或任何解释。

# 已统计登记单位（供参考）：
# {priority2_hint}

# 对话记录：
# {transcript}

# 请输出优先级1区域：
# """
#     response = client.chat.completions.create(
#         model=model,
#         messages=[
#             {"role": "system", "content": "你是中国税务专家助手"},
#             {"role": "user", "content": prompt}
#         ],
#         temperature=0.2
#     ).choices[0].message.content.strip()

#     return response

# # === 主流程 ===
# input_file = f'/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/data/{today}/多日重复来电预警合并结果{today}.xlsx'
# # input_file = f'/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/data/{today}/多日重复来电预警合并结果{today}_half_mon.xlsx'
# output_file = f'/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/data/{today}/附件3-重复来电预警情况表（{date}）.xlsx'
# # output_file = f'/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/data/{today}/多日重复来电预警合并结果{today}_half_mon.xlsx'
# date =f"{date_time}"

# # cd /cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/cxy-workplace/repeated_call/rolling
# # nohup python -u rolling_llm_summary.py > rolling_llm_summary.log 2>&1 &
# df_original = pd.read_excel(input_file, parse_dates=["通话开始时间"])
# df = df_original.copy()

# def clean_num(x):
#     return re.sub(r"[^\d]", "", str(x).replace(".0", "")).strip()

# df["来电号码"] = df["来电号码"].apply(clean_num)
# df_original["来电号码"] = df_original["来电号码"].apply(clean_num)
# df = df.dropna(subset=["大模型核心问题", "通话开始时间", "来电号码", "日期"])

# # ---------- 昨日预警映射 ----------
# yesterday_path = "/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/cxy-workplace/repeated_call/rolling/附件3-重复来电预警表（9月15日）.xlsx"
# yes_map = {}  # 号码 -> 昨日预警级别

# if USE_YESTERDAY_ALERT:
#     try:
#         df_yes = pd.read_excel(
#             yesterday_path,
#             sheet_name="预警结果",      # 直接读“预警结果”sheet
#             engine="openpyxl"
#         )
#         df_yes["号码"] = df_yes["号码"].astype(str).apply(clean_num)
#         # 直接建立 号码 -> 预警级别 映射
#         yes_map = df_yes.set_index("号码")["预警级别"].to_dict()
#     except Exception as e:
#         print("⚠️ 无法读取昨日预警级别列，跳过昨日映射：", e)
#         yes_map = {}
# else:
#     yes_map = {}

# level_weight = {"红灯预警": 3, "黄灯预警": 2, "蓝灯预警": 1}
# def max_level(a, b):
#     return a if level_weight.get(a, 0) >= level_weight.get(b, 0) else b


# # ---------- 计算 ----------
# records = []

# for number, grp in tqdm(df.groupby("来电号码"), desc="循环"):
#     # 1. 提炼核心问题
#     all_blocks = []
#     for txt in grp["大模型核心问题"]:
#         all_blocks += split_by_numbered_blocks(str(txt))
#     if not all_blocks:
#         continue
#     numbered = "\n".join(f"{i+1}. {q}" for i, q in enumerate(all_blocks))
#     core = extract_core_issue(numbered)

#     # 2. 相关时间
#     related_dates = []
#     for ts, txt in zip(grp["通话开始时间"], grp["大模型核心问题"]):
#         if any(judge_relevance(core, b) for b in split_by_numbered_blocks(str(txt))):
#             related_dates.append(ts)
#     if not related_dates:
#         continue
#     max_ts = max(related_dates)
#     cutoff = max_ts - pd.Timedelta(days=2)      # 3 天窗口：max-2, max-1, max
#     related_dates = [t for t in related_dates if t >= cutoff]
#     if not related_dates:                       # 极端情况：3 天内一条都没有
#         continue

#     # 3. 先算优先级2（raw_priority2）
#     raw_priority2 = "、".join(
#         df_original[df_original["来电号码"] == number]
#         .sort_values("通话开始时间", ascending=False)
#         .head(3)["登记单位"].dropna().astype(str).unique()
#     )

#     # 4. 收集与核心问题相关日期的转写文本
#     all_transcripts = []
#     all_biz_ids     = []
#     for ts, txt in zip(grp["通话开始时间"], grp["大模型核心问题"]):
#         if ts not in related_dates:
#             continue
#         matched = df_original[
#             (df_original["来电号码"] == number) &
#             (df_original["通话开始时间"] == ts)
#         ]
#         if not matched.empty:
#             row = matched.iloc[0]
#             trans = str(row.get("转写结果", "")).strip()
#             biz   = str(row.get("业务编号", "")).strip()
#             if trans:
#                 all_transcripts.append(trans)
#             if biz and biz != "nan":
#                 all_biz_ids.append(biz)

#     combined_transcript = "\n==============\n".join(all_transcripts)
#     combined_biz_ids    = "\n".join(all_biz_ids)

#     # 5. 生成优先级1
#     district_str = tier_1_priority_area(combined_transcript, raw_priority2)

#     # 6. 今日预警级别
#     # 把相关日期转成 DatetimeIndex
#     date_idx = pd.to_datetime(related_dates)
#     # print(date_idx)
#     # max_day：最后一天（日期最大值）当天的呼叫次数
#     max_day = (
#         pd.Series(1, index=date_idx)
#         .groupby(date_idx.date)
#         .sum()
#         .reindex([date_idx.date.max()], fill_value=0)
#         .iloc[0]
#     )

#     # max_3day：全部日期的 3 日滚动最大值
#     daily = pd.Series(1, index=date_idx).resample("D").sum().asfreq("D", fill_value=0)
#     max_3day = daily.rolling(3, min_periods=1).sum().max()
    
    
#     today_level = None

#     if USE_YESTERDAY_ALERT:
#         if max_day >= 10 or max_3day >= 16:
#             today_level = "红灯预警"
#         elif 7 <= max_day <= 9 or 10 < max_3day <= 15:
#             today_level = "黄灯预警"
#         elif 5 <= max_day <= 6 and  max_3day <= 10:
#             today_level = "蓝灯预警"
#         else:
#             today_level = None
#     else:
#         if max_day >= 10 :
#             today_level = "红灯预警"
#         elif 7 <= max_day <= 9 :
#             today_level = "黄灯预警"
#         elif 5 <= max_day <= 6 :
#             today_level = "蓝灯预警"
#         else:
#             today_level = None

    
#     yesterday_level = yes_map.get(number, "")
#     n = len(related_dates)

#     # 根据 n 决定昨日哪些级别可以参与比较
#     if n > 10:
#         allowed_yesterday = {"红灯预警", "黄灯预警", "蓝灯预警"}
#     elif n > 7:
#         allowed_yesterday = {"黄灯预警", "蓝灯预警"}
#     elif n > 5:
#         allowed_yesterday = {"蓝灯预警"}
#     else:
#         allowed_yesterday = set()

#     effective_yesterday = yesterday_level if yesterday_level in allowed_yesterday else ""
#     final_level = max_level(today_level or "", effective_yesterday)

#     # print("len(related_dates)=",len(related_dates))
#     # print("max_day=",max_day,"max_3day=",max_3day)
#     # print("today_level=",today_level)
#     # print("final_level=",final_level)
#     if final_level and number:
#         records.append({
#             "预警级别": final_level,
#             "号码": number,
#             "频度": len(grp),
#             "相同指向问题数": len(related_dates),
#             "反映问题": core,
#             "问题合集": numbered,
#             "可能涉及的区域（仅供参考）": district_str,
#             # "优先级2地域": raw_priority2,
#             "转写记录合集": combined_transcript,
#             "业务编号合集": combined_biz_ids, 
#             "日期": date,
#         })

# # ---------- 其它列 ----------
# df_out = pd.DataFrame(records)
# df_out = df_out[~df_out["反映问题"].astype(str).str.contains("专管员", na=False)]
# valid_numbers = set(df_out["号码"])

# number_to_taxpayer = {}
# number_to_org = {}
# for n in valid_numbers:
#     sub = df_original[df_original["来电号码"] == n].sort_values("通话开始时间", ascending=False)
#     if sub.empty:
#         number_to_taxpayer[n] = ""
#         number_to_org[n] = ""
#     else:
#         recent = sub.iloc[0]
#         number_to_taxpayer[n] = "" if pd.isna(recent.get("纳税人名称")) else str(recent["纳税人名称"]).strip()
#         number_to_org[n] = "" if pd.isna(recent.get("主管机关名称")) else str(recent["主管机关名称"]).strip()

# df_out["可能的纳税人信息(仅供参考)"] = df_out["号码"].map(number_to_taxpayer).fillna("")
# df_out["可能的主管税务机关(仅供参考)"] = df_out["号码"].map(number_to_org).fillna("")

# # ---------- 排序 ----------
# level_order = {"红灯预警": 0, "黄灯预警": 1, "蓝灯预警": 2}
# df_out["预警级别排序"] = df_out["预警级别"].map(level_order)
# df_out = df_out.sort_values(["预警级别排序", "频度"], ascending=[True, False]).drop(columns="预警级别排序")
# df_out = df_out[[
#     "预警级别", "号码",
#     "频度", "相同指向问题数", "反映问题", "问题合集",
#     "可能涉及的区域（仅供参考）", 
#     "可能的纳税人信息(仅供参考)", "可能的主管税务机关(仅供参考)",
#     "转写记录合集", "业务编号合集","日期"
# ]]

# df_original = df_original[df_original["来电号码"].isin(valid_numbers)]

# with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
#     df_original.to_excel(writer, index=False, sheet_name="清洗后原始数据")
#     df_out.to_excel(writer, index=False, sheet_name="每日重复来电预警情况表")

# # ---------- 代表问题 ----------
# red_issues = df_out[df_out["预警级别"] == "红灯预警"]["反映问题"].dropna().tolist()
# yellow_issues = df_out[df_out["预警级别"] == "黄灯预警"]["反映问题"].dropna().tolist()
# print("🔴 红灯预警代表问题：", extract_representative_issue(red_issues, "红灯预警"))
# print("🟡 黄灯预警代表问题：", extract_representative_issue(yellow_issues, "黄灯预警"))
# print(f"🔴 红灯：{len(red_issues)}  🟡 黄灯：{len(yellow_issues)}  🔵 蓝灯：{len(df_out[df_out['预警级别']=='蓝灯预警'])}  📊 总计：{len(df_out)}")
# nohup python rolling_llm_summary.py > summary_run.log 2>&1 &
USE_YESTERDAY_ALERT = False   # True：启用昨日预警逻辑；False：完全关闭
import pandas as pd
from openai import OpenAI
from tqdm import tqdm
import re
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import *

import sys
import os

# 获取当前文件的绝对路径
current_file_path = os.path.abspath(__file__)
# 获取当前文件所在目录
current_dir = os.path.dirname(current_file_path)
# 获取上一级目录
parent_dir = os.path.dirname(current_dir)

# 将父目录添加到系统路径
sys.path.insert(0, parent_dir)

# 现在可以导入public_config.py
import public_config

config_path = r"/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/config.json"
with open(config_path, 'r') as f:
    config = json.load(f)

today = TARGET_DATE         # 202XXXXX
date_time = TARGET_DATE_TIME # 202X-XX-XX
date = DATE_STR           # XX月XX日

DEBUG_MODE = False
client = OpenAI(base_url="http://127.0.0.1:9997/v1", api_key="None")

# === 安全字符串转换 ===
def safe_str(x):
    if pd.isna(x) or x is None or x == "":
        return ""
    s = str(x).strip()
    if s.lower() in ("nan", "none", "<na>", "null"):
        return ""
    return s

# === 工具：分割编号问题（1. ... 2. ...） ===
def split_by_numbered_blocks(text):
    parts = re.split(r"(?m)^(?=\d+\.\s*)", text.strip())
    return [p.strip() for p in parts if p.strip()]

# === 提炼核心反映问题（带重试） ===
def extract_core_issue(numbered, model='qwen2.5-instruct-int4', max_retries=2):
    if DEBUG_MODE:
        return ""
    for attempt in range(max_retries + 1):
        try:
            template = f"""
   你是一个税务业务专家。你的任务是：（1）阅读纳税人的每条来电记录并识别核心诉求；（2）整理归纳纳税人的共性诉求，提炼出一个核心问题；（3）判断该核心问题出现的频次；（4）判断来电人反映事项的涉及区域。

请阅读下列多条来电问题，提炼出唯一一条反映纳税人同类诉求的的核心问题，并满足以下全部要求：
【核心问题输出要求】
    - 只输出一句话，输出结果以问句形式呈现；
    - 必须体现逻辑连通性：用“因-果”或“场景-诉求”结构，让读者一眼看出“问题发生的场景 + 纳税人真正想解决的核心诉求”；
    - 禁止出现并列连词（如“及”“和”“与”），禁止罗列多条问题；
    - 避免空泛词（“相关问题”“如何操作”），必须给出确切场景 + 关键动作；
-- 提炼核心问题时要正确理解税务相关知识间的因果联系，避免类似答案：“个税记录更正及补缴公积金”，实际应为“因公积金补缴如何更正个人所得税汇算清缴？”。
-- 提炼的核心问题应当简洁准确，突出核心问题，应既没有个性化信息，又能提炼纳税人具体咨询内容。
-- 核心问题正确输出样例：“企业税款已缴纳但仍提示欠费，怎么处理？”，“查询历史工单处理进度”，“税控设备被锁定如何完成解锁？”，“公司给员工发放的生育津贴补差部分是否可以享受个人所得税免税政策？”，“税费种认定调整失败是什么原因导致的？如何解决？”，“外国人未在规定时间内进行居民纳税申报是否会涉及滞纳金？如何更正外籍人员的免税项目申报？”，“在上海有外建项目如果没有收到预收款需要预缴增值税和企业所得税吗？”，等等。
-- 只能提炼一个核心问题，上面的样例只是告诉你核心问题大致长啥样，你需要找到问题组中最重要、最能反映纳税人共性诉求的问题。
-- 提炼的核心问题颗粒度应细一点，避免把相关性不大的问题归纳为同一问题，避免问题杂糅、颗粒度大。
-- 提炼的核心问题应明确具体，避免将多个问题简单拼接在一起，造成问题杂糅、颗粒度大。
-- 【注意】可以根据实际情况输出1-2个问句。如果输出2个问句，那么这2个问句间必须有关联且为递进关系！！！！以下为几组核心问题命名示例：
示例1：“如何办理公司的税务迁移？如何处理公司迁移时未办结的税务事项？”
示例2：“个人如何代开劳务发票？个人代开劳务发票需要缴纳哪些税费及税率如何计算？”
示例3：“企业销售二手车如何申报增值税？如何申报销售二手车未开票收入的增值税？如果销售未抵扣进项税的二手车，应按什么税率申报增值税？”
示例4：“如何在电子税务局上操作签订或变更三方协议？如何解决三方协议验证失败的问题？”
示例5：“企业申请发票额度被驳回的原因是什么？如何解决因存在涉税风险被驳回的问题？”
示例6：“公司收到新股东实收资本后，应按实际收到的金额缴纳印花税吗？如何按次申报？”
示例7：“小规模纳税人如何在增值税申报表中申报3%税率的发票？如何申报未开票收入？”
示例8：“企业所得税申报中职工薪酬的填报口径是什么？如何区分和正确填写已计入成本费用的职工薪酬和实际支付给职工的应付职工薪酬？职工薪酬是否包含公司承担的社保和公积金？职工薪酬应填写累计数还是当季数？”
示例9：“跨境电商已缴纳国外销售税，国内申报时是否重复缴纳增值税?应该如何申报？”

【下述示例核心问题颗粒度大、问题杂糅，应避免！】
（1）“如何在电子税务局或社保客户端调整和查询社保缴费基数及申报记录？”因为缴费基数和申报记录是两个比较大的业务，且这两个模块不在一起，因此不能将社保费基数和申报记录简单合并为一个合并为问题!
（2）“公司进行各种交易或服务时如何确定应缴纳的税种及税率？”“企业如何享受税收优惠政策及具体操作流程是什么？”“海关缴款书相关问题如何处理？”，避免使用各种、各类、相关等宽泛的表述，颗粒度太粗了！
（3）“如何申请提高小规模纳税人的发票额度和恢复信用等级？”问题杂糅，既涉及发票额度调整，又涉及信用，不应该合成为一个核心问题!
（4）“如何处理红冲发票后的退税问题？”粒度太粗，这个业务一般是个人向税务机关申请代开发票后，然后红冲需要退税，可以明确一下，例如变成“如何申请个人代开发票红冲后产生的退税？”，具体情况结合实际语境细化！
（5）“如何在电子税务局更正个人所得税申报？”，在电子税务局无法更正个人所得税申报，可能描述的是自然人电子税务局，需要注意全称，企业申报个人所得税相关业务都是在自然人电子税务局办理。
（6）“企业与个人之间的股权转让涉及哪些税种及如何申报？”这个问题表述不明确，如果是个人把自己的股权转让给企业，应该是“个人股权转让”这个大类，如果是企业转让股权给个人，那么这个问题可以表述的更明确一点。
（7）“如何在自然人电子税务局申报个人股权转让的印花税和个税？”总结有误，自然人电子税务局上无法申报印花税，印花税申报应该在新电子税局上完成，提炼问题时应避免简单拼接和杂糅！
（8）“如何在电子税务局开具和下载各类完税证明？”粒度过粗，尽量明确到开具什么类别什么税种的完税证明，例如个人所得税完税证明等。
（9）“如何联系专管员确认股权转让相关问题？”粒度过粗，该类问题既找专管员、也要解决业务问题，首先对于此类问题提炼时应分清主次，该问题的核心是解决股权转让业务问题，重点提炼业务问题核心部分；其次“股权转让相关问题”粒度过大，应明确具体什么问题。重点是对业务问题的提炼，切记！！！！


    ### 示例1：
    输入：
    1. 如何解决因动迁房未注销导致的房产交易加税问题？
    2. 如何处理因动迁导致的房产未注销而需加税的问题？
    3. 如何处理拆迁后房产交易时的加税问题？
    4. 房屋交易是否需要缴纳额外税款？
    5. 如何处理动迁后未注销房产的交易涉税问题？
    6. 如何处理房产交易中的加税问题？
    7. 房屋交易的加税问题如何处理？
    8. 如何将业务转到杨浦区税务机关？
    9. 如何处理房子交易中遇到的税务问题？
    10. 如何办理房屋交易的税务手续？
    11. 如何处理因房屋拆迁导致的房产税问题？
    12. 如何办理房产交易的税务登记？
    13. 如何确认灵活就业人员的社保扣款是否成功？
    14. 如何处理房屋已不存在但系统仍显示需缴纳税款的问题？
    返回：
    如何解决动迁房未注销导致的房产交易加税问题？

    ### 示例2：
    输入：
    1. 如何申报境外利息收入的个人所得税？如何计算境外利息收入的个人所得税？
    2. 如何处理个税补申报时因已申报月份数据导致无法申报的问题？
    3. 如何在个税APP上补申报境外所得？
    4. 如何在个税APP申报境外利息收入？
    5. 如何在自然人电子税务局申报境外收入的个人所得税？
    6. 如何在自然人电子税务局更正境外所得的申报信息？
    7. 如何处理个税APP中更正申报后扣除项消失导致税额增加的问题？
    8. 如何处理更正申报后导致的个人所得税税额翻倍问题？
    9. 如何处理更正申报后专项附加扣除信息消失的问题？
    返回：
    如何申报或更正境外利息收入的个人所得税？

    ### 示例3：
    输入：
    1. 如何在电子税务局申请扣缴客户端的数据下载权限？
    2. 如何在电子税务局使用企业信息登录自然人业务？
    3. 如何解决个人所得税APP登录问题？如何处理电子税务局数据被覆盖问题？
    4. 如何解决电子税务局人脸识别认证时显示二维码无效的问题？
    5. 如何解决电子税务局人工服务无法使用的问题？
    6. 如何解决电子税务局登录后无法找到企业账户中心的问题？
    7. 如何在电子税务局切换身份并获取不同企业的纳税互动数据？
    8. 如何解决电子税务局显示“该地区暂未开放人工服务”的问题？
    返回：
    如何解决电子税务局登录、身份切换、人工服务无法使用问题？

    ### 示例4：
    输入：
    1. 境外居民如何申报缴纳股权转让的印花税？需要准备哪些材料？
    2. 境外企业办理股权转让印花税需要准备哪些材料？如何确认办税大厅的具体要求？
    3. 公司如何为外国受让方在电子税局上做临时税务登记并获取F码？外国受让方在电子税局上缴纳印花税时遇到支付问题，如何解决？如何在线下缴纳税款并获取缴款通知？
    4. 受让方需要准备哪些材料来缴纳股权转让的印花税？有限合伙企业是否可以按个人名义缴纳印花税？临时税务登记的流程和所需材料是什么？如果境外企业的公章无法带入境内，如何处理？网上缴纳印花税时遇到问题，是否需要到办税大厅办理？
    5. 如何在电子税局为境外非居民办理临时税务登记并缴纳印花税？如何在电子税局录入境外非居民的身份信息和合同信息？
    6. 有限合伙企业是否需要缴纳印花税？临时税务登记需要准备哪些资料？境外非居民企业进行临时税务登记是否必须提供公章？
    7. 查询专管员的联系方式？
    8. 公司进行股权转让需要做临时税务登记吗？临时税务登记需要哪些材料？经办人办理临时税务登记和印花税业务需要提供委托授权书吗？公章无法带至现场，如何处理盖章问题？临时税务登记表是否需要提前准备？
    9. 公司如何在电子税局上传境外非居民的电子版纳税证明？电子版纳税证明是否需要盖公章？

    返回：
    非居民企业股权转让如何缴纳印花税？
    ### 当前问题列表：
    {numbered}

    核心问题：
    """
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是税务知识专家。"},
                    {"role": "user", "content": template}
                ],
                temperature=0.2,
                timeout=120
            ).choices[0].message.content.strip()
            return resp
        except Exception as e:
            if attempt < max_retries:
                time.sleep(1 + attempt)
            else:
                print(f"❌ extract_core_issue 失败: {e}")
                return ""
    return ""

# === 判断子问题是否相关（带重试） ===
def judge_relevance(core, single_q, model='qwen2.5-instruct-int4', max_retries=1):
    if DEBUG_MODE:
        return True
    core = safe_str(core)
    single_q = safe_str(single_q)
    if not core or not single_q:
        return False
    for attempt in range(max_retries + 1):
        try:
            prompt = f"""
    你是一个税务业务专家。已知核心问题：“{core}”。请判断下面这一条问题是否在意图上属于为了解决该核心问题而提出的（只有部分相关也可以）：
    “{single_q}”
    如果相关，请只返回“true”，否则只返回“false”，注意，你无需返回任何解释，只能选择true或false中的某一个作为回复。
    若待判断问题为： "查询专管员的联系方式"，或类似问题，直接返回false。
    """
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是中国的税务知识专家，擅长税务问题的总结和评判。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0,
                timeout=60
            ).choices[0].message.content.strip().lower()
            return resp == "true"
        except Exception as e:
            if attempt < max_retries:
                time.sleep(1)
            else:
                print(f"⚠️ judge_relevance 失败: {e}")
                return False
    return False

# ----------------------------------------------------------------------------#   
def extract_representative_issue(issue_list, label="红灯预警", model="qwen2.5-instruct-int4"):
    if DEBUG_MODE:
        return ""
    if not issue_list:
        return ""
    combined_text = "。\n".join(issue_list)
    prompt = f"""
    你是一个税务领域的人工智能助手，请从下列多个纳税人反映的问题中，归纳出一条代表性最强、重复率最高、表达最清晰的问题陈述。
    - 只需输出一句话，字数控制在40字以内。
    - 不能出现“等”“多个问题”等模糊字眼。
    - 必须反映出具体的涉税问题或操作困难。
    - 不得罗列多个问题，仅保留一个代表性问题。

    问题列表（{label}）：
    {combined_text}

    请输出一条代表性问题（40字以内）：
    """
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是中国税务专家助手，擅长归纳问题"},
            {"role": "user", "content": prompt}
        ],
        temperature=0.2
    ).choices[0].message.content.strip()
    return response
# ====== 提炼优先级1地域（带重试） ======
def tier_1_priority_area(transcript: str, priority2_hint: str, model="qwen2.5-instruct-int4", max_retries=1):
    transcript = safe_str(transcript)
    priority2_hint = safe_str(priority2_hint)
    if DEBUG_MODE:
        return ""
    for attempt in range(max_retries + 1):
        try:
            prompt = f"""
你是一个税务领域的人工智能助手。
请根据下列对话记录以及“已统计登记单位”信息，判断纳税人本次系列来电中最有可能涉及的行政区划（优先级1区域）。
要求：
- 仅输出一个词，必须以“区”结尾，请在“黄浦区”、“徐汇区”、“静安区”、“普陀区”、“长宁区”、“虹口区”、“杨浦区”、“浦东新区”、“奉贤区”、“嘉定区”、“闵行区”、“松江区”、“金山区”、“青浦区”、“宝山区”、“崇明区”中选一个，只能选一个，且必须与我给出的区名一字不差。  
- 如果对话中纳税人明确提到某区，则以纳税人提到的为准。  
- 若纳税人未明确提及，则结合“已统计登记单位”与对话上下文综合判断。  
- 如果仍无法确定，则输出空字符串，不得输出多个区或任何解释。

已统计登记单位（供参考）：
{priority2_hint}

对话记录：
{transcript}

请输出优先级1区域：
"""
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是中国税务专家助手"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                timeout=60
            ).choices[0].message.content.strip()
            return response
        except Exception as e:
            if attempt < max_retries:
                time.sleep(1)
            else:
                print(f"⚠️ tier_1_priority_area 失败: {e}")
                return ""
    return ""

# === 昨日预警映射（保持不变）===
yesterday_path = "/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/cxy-workplace/repeated_call/rolling/附件3-重复来电预警表（9月15日）.xlsx"
yes_map = {}
if USE_YESTERDAY_ALERT:
    try:
        df_yes = pd.read_excel(yesterday_path, sheet_name="预警结果", engine="openpyxl")
        def clean_num(x):
            x = safe_str(x)
            return re.sub(r"[^\d]", "", x.replace(".0", "")).strip()
        df_yes["号码"] = df_yes["号码"].astype(str).apply(clean_num)
        yes_map = df_yes.set_index("号码")["预警级别"].to_dict()
    except Exception as e:
        print("⚠️ 无法读取昨日预警级别列，跳过昨日映射：", e)
        yes_map = {}
else:
    yes_map = {}

level_weight = {"红灯预警": 3, "黄灯预警": 2, "蓝灯预警": 1}
def max_level(a, b):
    return a if level_weight.get(a, 0) >= level_weight.get(b, 0) else b

# === 多线程处理函数 ===
def process_number(number, grp, df_original, today_dt):
    try:
        all_blocks = []
        for txt in grp["大模型核心问题"]:
            clean_txt = safe_str(txt)
            if clean_txt:
                all_blocks.extend(split_by_numbered_blocks(clean_txt))
        if not all_blocks:
            return None
        numbered = "\n".join(f"{i+1}. {q}" for i, q in enumerate(all_blocks))
        core = extract_core_issue(numbered)
        if not safe_str(core):
            return None

        # 筛选相关时间
        related_dates = []
        for ts, txt in zip(grp["通话开始时间"], grp["大模型核心问题"]):
            clean_txt = safe_str(txt)
            if not clean_txt:
                continue
            blocks = split_by_numbered_blocks(clean_txt)
            if any(judge_relevance(core, b) for b in blocks):
                related_dates.append(ts)
        if not related_dates:
            return None

        max_ts = max(related_dates)
        cutoff = max_ts - pd.Timedelta(days=2)
        related_dates = [t for t in related_dates if t >= cutoff]
        if not related_dates:
            return None

        # 优先级2地域
        raw_priority2 = "、".join(
            df_original[df_original["来电号码"] == number]
            .sort_values("通话开始时间", ascending=False)
            .head(3)["登记单位"].dropna().astype(str).unique()
        )

        # 收集转写和业务编号
        all_transcripts = []
        all_biz_ids = []
        for ts in related_dates:
            matched = df_original[
                (df_original["来电号码"] == number) &
                (df_original["通话开始时间"] == ts)
            ]
            if not matched.empty:
                row = matched.iloc[0]
                trans = safe_str(row.get("转写结果", ""))
                biz = safe_str(row.get("业务编号", ""))
                if trans:
                    all_transcripts.append(trans)
                if biz:
                    all_biz_ids.append(biz)

        combined_transcript = "\n==============\n".join(all_transcripts)
        combined_biz_ids = "\n".join(all_biz_ids)

        district_str = tier_1_priority_area(combined_transcript, raw_priority2)

        # 计算 max_day 和 max_3day
        date_idx = pd.to_datetime(related_dates)
        max_day = (
            pd.Series(1, index=date_idx)
            .groupby(date_idx.date)
            .sum()
            .reindex([date_idx.date.max()], fill_value=0)
            .iloc[0]
        )
        daily = pd.Series(1, index=date_idx).resample("D").sum().asfreq("D", fill_value=0)
        max_3day = daily.rolling(3, min_periods=1).sum().max()

        # 预警级别
        today_level = None
        if USE_YESTERDAY_ALERT:
            if max_day >= 10 or max_3day >= 16:
                today_level = "红灯预警"
            elif 7 <= max_day <= 9 or 10 < max_3day <= 15:
                today_level = "黄灯预警"
            elif 5 <= max_day <= 6 and max_3day <= 10:
                today_level = "蓝灯预警"
        else:
            if max_day >= 10:
                today_level = "红灯预警"
            elif 7 <= max_day <= 9:
                today_level = "黄灯预警"
            elif 5 <= max_day <= 6:
                today_level = "蓝灯预警"

        if not today_level:
            return None

        yesterday_level = yes_map.get(number, "")
        n = len(related_dates)

        if n > 10:
            allowed_yesterday = {"红灯预警", "黄灯预警", "蓝灯预警"}
        elif n > 7:
            allowed_yesterday = {"黄灯预警", "蓝灯预警"}
        elif n > 5:
            allowed_yesterday = {"蓝灯预警"}
        else:
            allowed_yesterday = set()

        effective_yesterday = yesterday_level if yesterday_level in allowed_yesterday else ""
        final_level = max_level(today_level or "", effective_yesterday)

        if final_level and number:
            return {
                "预警级别": final_level,
                "号码": number,
                "频度": len(grp),
                "相同指向问题数": len(related_dates),
                "反映问题": core,
                "问题合集": numbered,
                "可能涉及的区域（仅供参考）": district_str,
                "转写记录合集": combined_transcript,
                "业务编号合集": combined_biz_ids,
                "日期": date_time,
            }
        return None

    except Exception as e:
        print(f"⚠️ 号码 {number} 处理出错: {e}")
        return None

# === 主流程 ===
input_file = f'/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/data/{today}/多日重复来电预警合并结果{today}.xlsx'
output_file = f'/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/data/{today}/附件3-重复来电预警情况表（{date}）.xlsx'

df_original = pd.read_excel(input_file, parse_dates=["通话开始时间"])
df = df_original.copy()

def clean_num(x):
    x = safe_str(x)
    return re.sub(r"[^\d]", "", x.replace(".0", "")).strip()

df["来电号码"] = df["来电号码"].apply(clean_num)
df_original["来电号码"] = df_original["来电号码"].apply(clean_num)
df = df.dropna(subset=["大模型核心问题", "通话开始时间", "来电号码", "日期"])

# ---------- 多线程处理 ----------
grouped = list(df.groupby("来电号码"))
records = []

# ⚠️ 降低并发数避免 vLLM 崩溃
with ThreadPoolExecutor(max_workers=3) as executor:
    future_to_number = {
        executor.submit(process_number, number, grp, df_original, pd.to_datetime(today, format="%Y%m%d")): number
        for number, grp in grouped
    }
    for fut in tqdm(as_completed(future_to_number), total=len(grouped), desc="处理号码"):
        try:
            result = fut.result(timeout=300)
            if result is not None:
                records.append(result)
        except Exception as e:
            number = future_to_number[fut]
            print(f"⚠️ 号码 {number} 异常: {e}")

# ---------- 后处理 ----------
df_out = pd.DataFrame(records)
df_out = df_out[~df_out["反映问题"].astype(str).str.contains("专管员", na=False)]
valid_numbers = set(df_out["号码"])

number_to_taxpayer = {}
number_to_org = {}
for n in valid_numbers:
    sub = df_original[df_original["来电号码"] == n].sort_values("通话开始时间", ascending=False)
    if not sub.empty:
        recent = sub.iloc[0]
        taxpayer = safe_str(recent.get("纳税人名称", ""))
        org = safe_str(recent.get("主管机关名称", ""))
        number_to_taxpayer[n] = taxpayer
        number_to_org[n] = org
    else:
        number_to_taxpayer[n] = ""
        number_to_org[n] = ""

df_out["可能的纳税人信息(仅供参考)"] = df_out["号码"].map(number_to_taxpayer).fillna("")
df_out["可能的主管税务机关(仅供参考)"] = df_out["号码"].map(number_to_org).fillna("")

level_order = {"红灯预警": 0, "黄灯预警": 1, "蓝灯预警": 2}
df_out["预警级别排序"] = df_out["预警级别"].map(level_order)
df_out = df_out.sort_values(["预警级别排序", "频度"], ascending=[True, False]).drop(columns="预警级别排序")

df_out = df_out[[
    "预警级别", "号码",
    "频度", "相同指向问题数", "反映问题", "问题合集",
    "可能涉及的区域（仅供参考）", 
    "可能的纳税人信息(仅供参考)", "可能的主管税务机关(仅供参考)",
    "转写记录合集", "业务编号合集", "日期"
]]

df_original_filtered = df_original[df_original["来电号码"].isin(valid_numbers)]

with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
    # df_original_filtered.to_excel(writer, index=False, sheet_name="清洗后原始数据")
    df_out.to_excel(writer, index=False, sheet_name="每日重复来电预警情况表")
with pd.ExcelWriter(f"./output/附件3-重复来电预警情况表（{date}）.xlsx", engine="openpyxl") as writer:
    # df_original_filtered.to_excel(writer, index=False, sheet_name="清洗后原始数据")
    df_out.to_excel(writer, index=False, sheet_name="每日重复来电预警情况表")

# ---------- 代表问题 ----------
red_issues = df_out[df_out["预警级别"] == "红灯预警"]["反映问题"].dropna().tolist()
yellow_issues = df_out[df_out["预警级别"] == "黄灯预警"]["反映问题"].dropna().tolist()
blue_issues = df_out[df_out["预警级别"] == "蓝灯预警"]["反映问题"].dropna().tolist()

# print("🔴 红灯预警代表问题：", extract_representative_issue(red_issues, "红灯预警"))
# print("🟡 黄灯预警代表问题：", extract_representative_issue(yellow_issues, "黄灯预警"))
# print("🔵 蓝灯预警代表问题：", extract_representative_issue(blue_issues, "蓝灯预警"))
print(f"🔴 红灯：{len(red_issues)}  🟡 黄灯：{len(yellow_issues)}  🔵 蓝灯：{len(blue_issues)}  📊 总计：{len(df_out)}")
# nohup python -u rolling_llm_summary.py > rolling_llm_summary.log 2>&1 &