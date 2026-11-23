const policy = '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:">';

export function HtmlPreview({ value }: { value: string }) {
  return <iframe className="html-preview" title="HTML 模板预览" sandbox="" srcDoc={policy + value} />;
}
