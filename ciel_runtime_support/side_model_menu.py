"""Side model selection reuses the provider catalog without changing main model."""


def rows(config, catalog_rows, *, field='model'):
    labels, values = catalog_rows
    current = config.get('work_side_intake', {}).get(field, 'inherit')
    choices = [('inherit — follow main model', 'inherit')]
    seen = {'inherit', '__custom__', 'back'}
    for label, value in zip(labels, values):
        if value in seen:
            continue
        seen.add(value)
        choices.append((label.lstrip('* '), value))
    if current not in seen:
        choices.append((current, current))
    choices.extend([('Custom side model id...', '__edit__'), ('Back', 'back')])
    return ([('* ' if value == current else '  ') + label for label, value in choices],
            [value for _, value in choices])
