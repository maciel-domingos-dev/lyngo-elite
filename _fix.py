with open('app.py', 'rb') as f:
    c = f.read()

old = b'.link-row-compact {\n    display: flex;\n    align-items: center;\n    gap: 0.5rem;\n    padding: 0.28rem 0;\n    border-bottom: 1px solid rgba(244,114,182,0.06);\n    min-width: 0;\n}'

new = b'.link-row-compact {\n    display: flex;\n    align-items: center;\n    gap: 0.5rem;\n    padding: 0.28rem 0;\n    border-bottom: 1px solid rgba(244,114,182,0.06);\n    min-width: 0;\n    min-height: 2.8rem;\n}\n/* Alinha verticalmente as colunas dos botoes de link */\n.lk-action-row [data-testid="stColumn"] {\n    display: flex !important;\n    align-items: center !important;\n    padding-top: 0 !important;\n    padding-bottom: 0 !important;\n}\n.lk-action-row [data-testid="stColumn"] > div {\n    width: 100% !important;\n    display: flex !important;\n    align-items: center !important;\n}'

if old in c:
    c = c.replace(old, new)
    with open('app.py', 'wb') as f:
        f.write(c)
    print('OK! CSS corrigido!')
else:
    print('ATENCAO: nao encontrado')