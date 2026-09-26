import {readFileSync} from "node:fs";
import {resolve} from "node:path";

test('Vite entry does not contain unprocessed EJS or static Umami template markup',()=>{
  const html=readFileSync(resolve(process.cwd(),'frontend','index.html'),'utf8');
  expect(html).not.toContain('<%');
  expect(html).not.toContain('%>');
  expect(html).not.toContain('VITE_UMAMI_SCRIPT_URL');
});
