from pathlib import Path
import re
for html,js in [('index.html','app.js'),('experiments.html','experiments.js')]:
 text=Path('nextbus/web/'+html).read_text(encoding='utf-8-sig')
 ids=set(re.findall(r'id="([^"]+)"',text))
 source=Path('nextbus/web/'+js).read_text(encoding='utf-8-sig')
 refs=set(re.findall(r"(?:element|\$)\('([^']+)'\)",source))
 assert len(text)>1000 and not refs-ids,(html,refs-ids)
 print(html,'all static JS element references present')
