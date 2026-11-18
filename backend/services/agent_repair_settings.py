"""Bounded message-scoped repair preferences; never execution authorization."""
def normalize(value):
    if not isinstance(value,dict):return None
    method=value.get('detection_method','ssim')
    if method not in ('absdiff','ssim','smart'):raise ValueError('Agent 检测方法无效')
    result={'detection_method':method}
    for key,default in [('inspection_only',False),('tone_weight',True),('skip_completed',True)]:
        v=value.get(key,default)
        if type(v) is not bool:raise ValueError('Agent 设置须为布尔值：'+key)
        result[key]=v
    return result
