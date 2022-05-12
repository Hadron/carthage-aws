def find_tags(tl, tn):
    tl = [ x for x in tl if x['Key'] == tn  ]
    values = [ x['Value'] for x in tl ]
    return values

def find_name_from_tags(tl):
    values = find_tags(tl)
    if len(values) != 1:
        raise ValueError(f'unable to find Name in {tl}: got {values}')
    return values[0]

def has_tag_matching(tl, k, v):
    for t in tl:
        if t['Key'] == k and t['Value'] == v:
            return True
    return False

