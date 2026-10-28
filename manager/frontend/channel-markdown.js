import {marked} from 'marked';
import DOMPurify from 'dompurify';
window.CorkiChannelMarkdown = content => {
  const html = DOMPurify.sanitize(marked.parse(content, {gfm:true, breaks:true}), {
    USE_PROFILES:{html:true}, FORBID_TAGS:['img','video','audio','iframe','style'],
    FORBID_ATTR:['style']
  });
  const template = document.createElement('template');
  template.innerHTML = html;
  for (const a of template.content.querySelectorAll('a')) { a.target='_blank'; a.rel='noopener noreferrer'; }
  return template.innerHTML;
};
