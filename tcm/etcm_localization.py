"""Versioned Chinese display terms. Original ETCM values remain authoritative source text."""
import re

VERSION='etcm-zh-display-v1'
LABELS={'name_pinyin':'拼音名','latin_name':'拉丁名','english_name':'英文名','family':'科名',
        'habitat':'分布/产地','medicinal_part':'药用部位','properties':'药性','flavors':'药味',
        'meridians':'归经','efficacy_class':'原库功效分类','indications':'来源记载的功效/适应证',
        'reference_pmids':'关联PMID','cross_references':'外部数据库标识'}
TERMS={
 'properties':dict(zip(['Cold','Cool','Even','Great cold','Great hot','Hot','Mildly cold','Mildly cool','Mildly warm','Warm'],
                       ['寒','凉','平','大寒','大热','热','微寒','微凉','微温','温'])),
 'flavors':dict(zip(['Astringent','Bitter','Bland','Mildly astringent','Mildly bitter','Mildly pungent','Mildly salty','Mildly sour','Mildly sweet','Pungent','Salty','Sour','Sweet'],
                   ['涩','苦','淡','微涩','微苦','微辛','微咸','微酸','微甘','辛','咸','酸','甘'])),
 'medicinal_part':{'Root':'根','Rhizome':'根茎','Whole Herb':'全草','Whole Grass':'全草','Herb':'草本部分',
   'Fruit':'果实','Seed':'种子','Leaf':'叶','Stem':'茎','Bark':'树皮','Root Bark':'根皮','Flower':'花',
   'Flower Bud':'花蕾','Flower Buds':'花蕾','Flowerbud':'花蕾','Aerial Parts':'地上部分','Bulb':'鳞茎',
   'Tuber':'块茎','Tuberoid':'块状体','Bud':'芽','Branch':'枝','Branchlet':'小枝','Resin':'树脂',
   'Pollen':'花粉','Heartwood':'心材','Peel':'果皮','Pericarp':'果皮','Aril':'假种皮','Achene':'瘦果'},
 'family':{'Compositae':'菊科','Asteraceae':'菊科','Leguminosae':'豆科','Fabaceae':'豆科',
   'Labiatae':'唇形科','Lamiaceae':'唇形科','Ranunculaceae':'毛茛科','Liliaceae':'百合科','Rutaceae':'芸香科',
   'Apiaceae':'伞形科','Umbelliferae':'伞形科','Rosaceae':'蔷薇科','Rubiaceae':'茜草科','Euphorbiaceae':'大戟科',
   'Solanaceae':'茄科','Magnoliaceae':'木兰科','Cucurbitaceae':'葫芦科','Poaceae':'禾本科','Gramineae':'禾本科',
   'Berberidaceae':'小檗科','Papaveraceae':'罂粟科','Zingiberaceae':'姜科','Lauraceae':'樟科',
   'Araliaceae':'五加科','Orchidaceae':'兰科','Polygonaceae':'蓼科','Scrophulariaceae':'玄参科'},
 'efficacy_class':{'Astringent medicinal':'收涩药','Blood-activating and stasis-resolving medicinal':'活血化瘀药',
   'Calm panting medicinal':'平喘药','Dampness-draining diuretic medicinal':'利水渗湿药','Dampness-resolving medicinal':'化湿药',
   'Destroying parasites to relieve itching medicinal':'杀虫止痒药','Digestant medicinal':'消食药',
   'Draw out the poison and promoting granulation medicinal':'拔毒生肌药','Emetic medicinal':'涌吐药',
   'Exterior-releasing medicinal':'解表药','Heat-clearing and detoxicating medicinal':'清热解毒药',
   'Heat-clearing medicinal':'清热药','Hemostatic (medicinal)':'止血药','Interior-warming medicinal':'温里药',
   'Liver-pacifying and wind-extinguishing medicinal':'平肝息风药','Orifice-opening medicinal':'开窍药',
   'Purgative medicinal':'泻下药','Qi-regulating medicinal':'理气药','Resolve phlegm':'化痰药',
   'Suppress cough':'止咳药','Tonifying deficiency medicinal':'补虚药','Tranquillizing medicinal':'安神药',
   'Wind-dampnessdispelling medicinal':'祛风湿药','Worm-expelling medicinal':'驱虫药',
   'Medicinal for relieving pain':'止痛药','Medicinal for arresting convulsions;analgesic':'止痉；镇痛药',
   'Medicinal for detoxification; edema-alleviating':'解毒；消肿药',
   'Medicinal for detoxification; parasiticide; drying dampness and relieving itching':'解毒；杀虫；燥湿止痒药'},
 'habitat':dict(zip(['AnHui','ZheJiang','JiangXi','FuJian','GuangDong','GuangXi','GuiZhou','YunNan','HaiNan','XiZang','TaiWan',
   'XinJiang','HeiLongJiang','JiLin','LiaoNing','JiangSu','ShanDong','ShangHai','HeNan','HuNan','HuBei','SiChuan','ChongQing',
   'HeBei','ShanXi','ShaanXi','GanSu','QingHai','NingXia','NeiMengGu','BeiJing','TianJin','China'],
   ['安徽','浙江','江西','福建','广东','广西','贵州','云南','海南','西藏','台湾','新疆','黑龙江','吉林','辽宁','江苏','山东','上海',
    '河南','湖南','湖北','四川','重庆','河北','山西','陕西','甘肃','青海','宁夏','内蒙古','北京','天津','中国']))}
for english,chinese in [('Bladder','膀胱经'),('Gallbladder','胆经'),('Heart','心经'),('Kidney','肾经'),
                        ('Large intestine','大肠经'),('Liver','肝经'),('Lung','肺经'),('Pericardium','心包经'),
                        ('Small intestine','小肠经'),('Spleen','脾经'),('Stomach','胃经'),('Triple burner','三焦经')]:
    TERMS.setdefault('meridians',{})[english]=chinese
    TERMS['meridians'][english+' meridian']=chinese
TERMS['meridians'].update({'LargeIntestine':'大肠经','SmallIntestine':'小肠经'})
TERMS['indications']={'Tonifying the spleen and benefiting qi':'健脾益气',
    'Eliminating dampness and alleviating water retention':'燥湿利水','Hidroschesis':'止汗','Calming fetus.':'安胎',
    'Calming fetus':'安胎'}


def dictionary_translation(field, value):
    if not isinstance(value,str) or not value.strip():return None
    lookup={re.sub(r'\s+',' ',k).casefold():v for k,v in TERMS.get(field,{}).items()}
    parts=[s.strip() for s in value.split(',')]
    translated=[lookup.get(re.sub(r'\s+',' ',part).casefold()) for part in parts]
    if translated and all(translated):
        return {'text':'、'.join(translated),'method':'local_dictionary','status':'dictionary_display',
                'rule_version':VERSION,'human_reviewed':False}
    return None
