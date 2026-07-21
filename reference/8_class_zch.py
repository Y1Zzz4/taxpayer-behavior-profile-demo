import pandas as pd
from openai import OpenAI
import json, re
from collections import Counter, defaultdict
from tqdm import tqdm
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed


client = OpenAI(base_url="http://127.0.0.1:9997/v1", api_key="not used actually")
def call_model_api(template=None, response_format=None, model="qwen2.5-instruct-int4"):
    if not template:
        raise ValueError("Template content must be provided.")
    
    messages = [
        {"role": "system", "content": "你是税务局的高级税务数据处理专家，熟悉税务热线工作流程及业务记录情况。"},
        {"role": "user", "content": template}
    ]
    
    try:
        content = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.01,
            response_format={"type": "json_object"}
        ).choices[0].message.content
    except Exception as e:
        print(e)
        content = ""
    return content


def process_row(raw_content):
    
    template = f"""
    
        你将收到一段纳税服务热线的税务人与纳税人的对话记录。

        请按以下步骤进行分析，并以JSON格式返回结果：
        根据对话内容，将纳税人的咨询问题归类为以下类别之一，一定要基于语义进行判断，而不是简单的关键词匹配
        - 严格遵循以下8类标签定义，不得出现8个以外的标签，不得增减或擅自扩展
        - 此次打标签遵循“一对一，多对多”原则，即一类场景问题只能打「一个」标签，纳税人问多个场景问题可以打「多个」标签。例如，“来电人咨询社保费相关业务操作，然后因为其他事项再询问专管员联系方式”，同时属于“操作辅导类”和“涉税查询类”；“来电人查询社保费退费进度”，只属于“涉税查询类”。
        - 如果涉及多个标签的情况，应当按下面标签定义的顺序进行输出，多个标签之间用", "分隔。
        - 应当仅提取纳税人意图，若为税务人主动提出的问题或需求，可以忽略。应当提取纳税人最主要意图的问题。例如纳税人问了多个递进的问题，例如先问了税款缴纳所需材料（操作辅导类），又因为税款缴纳事项询问专管员联系方式（涉税查询类）。应当理解为询问专管员联系方式是为了更好地进行税款缴纳，因此整个对话记录应当被归为操作辅导类，而非二者皆是。与“一对一，多对多”的区别在于前后是否是为了同一件事。
        (1)政策咨询类   
        - 各类税收征收管理规定及政策要求；
        - 各税种的税率、计算方法和征收方式的详细说明；
        - 关于减税、免税以及其他优惠政策的适用条件和具体执行细则。
        - 出现滞纳金相关问题，只能归结为“政策咨询类”，不能归为其他几类问题；
        - 注意：发票如何开具属于“操作辅导类”，不属于“政策咨询类”。
        (2)操作辅导类    
        - 纳税人咨询业务办理类问题（如税（费）申报类业务的操作路径、更正、撤销、填表方式等问题；
        - 新电子税务局、电子发票服务平台、全国增值税发票查验平台、自然人电子税务局、个税app、社保费管理客户端等平台的下载、登录、修改密码及操作问题；
        - 发票业务类问题，比如开具、红冲、交付、勾选确认、下载打印、单轨切换、代开等；
        - 证明开具类问题，比如税收完税证明、纳税记录、中国税收居民身份证明、出口退（免）税证明、中央非税收入统一票据、税收缴款书等；
        - 非正常关联关系解除问题，例如纳税人已离职、或企业已注销，纳税人仍然与该企业存在关联关系；
        - 纳税信用补评、复评、修复类问题；
        - 上述咨询所需材料等问题。
        - 坐席告知操作路径或主动告知其他解决途径，包括咨询专管员、审批所、大厅、技术服务商；
        - 对话中坐席建议来电人先行协商，协商不成再来电派单处理的，不应归为“工单/拉起类”，应为操作辅导类；
        - 注意：发票如何开具属于“操作辅导类”，不属于“政策咨询类”。
        - 注意：纳税人主要想办理相关业务，坐席引导至其他部门办理的，应为“操作辅导类”，不属于“涉税查询类”。 
        (3) 工单/拉起类    
        - 出现关键词“拉起”或“反向拉起”，都认为属于工单/拉起类。
        - 一旦生成工单，即坐席通过工单帮其办理，就认为是工单/拉起类。
        - 坐席没有记录工单，但记录了来电人的基本信息，然后通过内部流转向上反馈，例如承诺后台老师、科室、所里回电，也认为是工单/拉起类。如果坐席说可以派单处理，但实际并未索要信息派单，则不属于“工单/拉起类”。
        - 不属于以上三种，均不认为属于工单/拉起类。
        - 不要轻易将纳税人的诉求纳入“工单/拉起类”，请先判断是否属于其他类别。
        (4) 涉税查询类
        - 纳税人查询公开信息，包括征期查询，查询专管员联系方式（关键词：查专管员电话、查中国人电话，查询中管员电话）、技术服务商的联系方式，查询办税大厅、某区某税务所某老师的联系方式、地址、办公时间等；
        - 查询个性化信息，比如，办税进度及结果信息查询，如发票查询查验、查询催办（额度审批、退税（费）进度、各类申请等，但未形成工单）、税务变更、注销、新版纳税人套餐审批、负面清单处理、股权转让审批、申报结果查询、申诉进度查询、催办工单进度、办税异常查询、社保关联关系查询、社保费缴费工资处理结果查询等，历史办税操作查询，历史工单记录办理进度查询。
        - 查询历史工单信息。查询历史工单信息的优先级优先于投诉举报类，只要是查询历史工单信息，不管有没有出现“投诉”“举报”等词，都只能归为“涉税查询类”，不能分类成“投诉举报类”！
        - 其他涉税信息查询，包括企业纳税人信息一户式查询、出口商品代码及退税率查询、车船税信息查询、重大税收违法案件信息查询、全国税收票证查验、行政许可信息查询、企业状态查询、纳税信用查询等；
        - 注意：对于额度审批、退税（费）进度、各类申请等问题，如果未形成工单，就属于涉税查询类；如果生成了工单，则属于工单/拉起类。这类问题可以先判断是否是工单/拉起类，如果不是就是涉税查询类。
        - 注意：纳税人主动查询、询问的属于“涉税查询类”；座席主动建议联系某某人或某某机关的不属于“涉税查询”，关键词：座席表示“建议联系某某某”“可以联系某某某”“推荐联系某某某”的都不属于“涉税查询类”。即“涉税查询类”的前提条件是纳税人主动询问，坐席主动告知的不算。注意语境！
        - 注意：归为“涉税查询类”后，不能再归为其他标签；如果出现电话转接，则只能属于“其他类”！！！除非是问了两个不相关的问题，例如，“来电人咨询社保费相关业务操作，然后再询问专管员联系方式”，同时属于“操作辅导类”和“涉税查询类”；“来电人查询社保费退费进度”，只属于“涉税查询类”。
        - 如果咨询往期投诉举报历史工单处理进度，应当属于涉税查询类，不能归为投诉举报类。而如果咨询如何投诉，则属于投诉举报类。
        - 如果咨询系统中出现词的含义，应当属于涉税查询类，如果咨询具体系统操作，应当属于操作辅导类。
        - 查询非税务部门的联系方式不属于涉税查询类，应属于其他类。
        - 注意：纳税人询问坐席如何办理业务，坐席表示需要咨询专管员或者大厅、并告知相应电话的，不属于“涉税查询类”，应归为其他业务标签。
        - 注意：若纳税人询问的税务机关联系方式与后其表达想要办理的业务有强关联，还是遵循业务优先的原则，应归为其他业务标签。
        - 注意：查询非税部门的联系方式（如查工商、社保、公积金等部门联系方式）不属于“涉税查询类”，属于“其他类”。
        - 注意：纳税人主要想办理相关业务，坐席引导至其他部门办理的，应为“操作辅导类”，不属于“涉税查询类”。
        (5) 系统异常类
        -   纳税人反馈税务系统出现故障、异常、页面空白、报错等问题。其中税务系统包括电子税务局、电子发票服务平台、自然人电子税务局、个税APP、社保费管理客户端、市局官方门户网站、全国增值税发票查验平台等信息化平台
        -   注意：纳税人对具体系统操作进行咨询的操作指导类问题，或者对系统数据有异议的问题等不属于此类。
        (6) 投诉举报类
        - 纳税人对税收违法行为进行举报，包括举报企业应开具而未开具发票、未申报办理税务登记、涉嫌偷税（逃避缴纳税款）、逃避追缴欠税、骗税、虚开、伪造、变造发票、涉嫌欠缴社保费等；
        - 纳税服务投诉，包括投诉税务机关及其税务人员在履行纳税服务职责过程中未提供规范、文明的纳税服务或者有其他侵犯其合法权益的情形等
        - 只有对于具体问题的投诉或者如何投诉举报应当被归为投诉举报类；如果没有具体问题，或者是对以前投诉问题、投诉举报工单信息的查询、操作或补充，应当被归为涉税查询类、操作辅导类或其他符合定义的类。
        - 注意：纳税人投诉企业未参保或欠保等问题，属于社保部门职责范畴，非税务问题，应该归类为“其他类”，不属于“投诉举报类”。
        (7) 意见建议类
        - 纳税人对税收政策、办税流程、系统优化等提出意见建议；
        (8) 其他类
        - 电话转接类也属于“其他类”，关键词“这边帮您转到”，“这边帮您转过去”，“我帮您转到”，“我帮您转过去”，“这边是XX先行联系部门”，“这边上海市税务局先行联系部门”，无论前边咨询了什么内容，只要有电话转接到税务部门，则只能归为“其他类”，不能再打成其他标签！但转接电话中，转接技术服务商等非税务部门不归类为“其他类”，正常按照八分类进行分类。
        - 指定座席接听类（通常是纳税人来电指定某座席接听电话，关键词“找某某工号座席接听”），也属于“其他类”。
        - 座席回拨类（通常是座席针对纳税人前期来电或咨询问题主动回拨给纳税人，即税务人主动回拨的才算，座席开头表示“我是某某区12366座席”），也属于“其他类”。
        - 纳税人咨询其他非税务问题（如咨询面试资格复审材料、财会类、落户类问题等等）、咨询税收筹划类问题等；
        - 对话记录内容无意义、只是打招呼等内容，
        - 不属于以上七类的其他咨询记录。
        - 注意：查询专管员、技术服务商、某区某税务所某老师、办税大厅等的联系方式或地址，属于“涉税查询类”，不属于“其他类”。
        - 注意：查询非税部门的联系方式（如查工商、社保、公积金等部门联系方式）不属于“涉税查询类”，属于“其他类”。
        - 注意：纳税人投诉企业未参保、欠保，或取消参保登记等问题，属于社保部门职责范畴，非税务问题，应该归类为“其他类”，不属于“投诉举报类”。
        返回示例请参考以下格式：
        {{
            "categories": "类别",         # 如果存在两个类别，类别之间请用", "进行分割，即一个英文逗号和一个空格。
        }}
        返回的应该是标准的JSON格式，请严格按照上述示例进行输出，JSON内容应该是严格的字符串类型。
        对于字符串，必须使用英文双引号进行包含。必须使用英文双引号进行包含。必须使用英文双引号进行包含。
        
        特别注意：最多只能输出两个类别！最多只能输出两个类别！最多只能输出两个类别！不要超过两个。

        对话记录内容如下：
        {raw_content}
    
    """
    
    try:
        llm_output = call_model_api(
            template=template,
            response_format="json_object"
        )
    except Exception as e:
        print(f"[ERROR] 调用 LLM 失败：{e}")
        return ""

    # 打印原始返回，便于调试
    print(f"[LLM 原始返回] {llm_output}")

    # 解析 JSON，仅取 category 字段
    try:
        result = json.loads(llm_output)
        cats = result.get("categories", [])
        return cats
    except Exception as e:
        print(f"[ERROR] 解析 JSON 失败：{e}")

    return []



if __name__ == "__main__":
    
    
    # input_file = "/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/data/20260100/转写结果20260100.xlsx"
    # output_file = "/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/data/20260100/20260100八大类.xlsx"
    
    # input_file = f"/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/data/3月/2025年4月“个税综合所得汇算”相关业务明细表.xlsx"
    # output_file = f"/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/data/3月/2025年4月“个税综合所得汇算”相关业务明细表（八大类）.xlsx"
        
    input_file = f"/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/data/6月/6月数据（含核心问题）.xlsx"
    output_file = f"/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/data/6月/6月数据八大类.xlsx"
    
    # input_file = f"/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/data/2025年5月相关业务表/2025年5月“企税年度汇算清缴”相关业务明细.xlsx"
    # output_file = f"/cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/data/2025年5月相关业务表/2025年5月“企税年度汇算清缴”相关业务明细（八大类）.xlsx"


    
    # cd /cpfs01/projects-HDD/cfff-8f1c54eecb30_HDD/bjj_24212010001/Daily_report/LLM_classification
    # nohup python -u 8_class_zch.py > ./eight_logs/out.log 2>&1 &  
    
    df_input = pd.read_excel(
        input_file,
        # nrows = 10
        # sheet_name="汇总结果"
        )
    results_dict = {}
    # # 1、对话记录+业务内容+模型核心问题
    # texts = (df_input["转写结果"].astype(str) + "\n坐席记录业务内容：" + df_input["CASEYWNR"].astype(str) + "\n纳税人核心问题" + df_input["大模型核心问题"].astype(str) ).tolist()
    
    # # 2、对话记录+业务内容
    texts = (df_input["转写结果"].astype(str) + "\n坐席记录业务内容：" + df_input["业务内容"].astype(str)).tolist()
    # texts = ( "坐席记录诉求：" + df_input["CASEYWNR"].astype(str) + "\n坐席记录回答：" + df_input["CASEDFNR"].astype(str) ).tolist()
    
    # # 3、对话记录
    # texts = (df_input["原始诉求内容"].astype(str)).tolist()
    
    # 4、对话记录+人工核心问题
    # texts = (df_input["转写结果"].astype(str) + "\n纳税人核心问题：" + df_input["热点问题提炼"].astype(str)).tolist()

    # # 5、大模型核心问题
    # texts = (df_input["核心问题"].astype(str)).tolist()
    
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_text = {executor.submit(process_row, text): text for text in texts}
        for future in tqdm(as_completed(future_to_text), total=len(texts)):
            try:
                cats = future.result()  # 获取返回的 cats 列表
                text = future_to_text[future]  # 获取对应的 text
                results_dict[text] = cats  # 将 cats 列表与对应的 text 存储到结果字典中
            except Exception as e:
                print(f"Error processing {future_to_text[future]}: {e}")
                results_dict[future_to_text[future]] = []  # 如果出错，存储一个空列表

    predictions = [results_dict[text] for text in texts]

    # 3. 将预测结果写入一个新列“专题类别”
    df_input["大模型八大类类别"] = predictions
    # try:
    #     df_input = df_input.drop(columns=['答复内容'])
    # except:
    #     pass

    # 4. 保存为新的 Excel
    df_input.to_excel(output_file, index=False)

    print("已完成分类，结果已保存。")