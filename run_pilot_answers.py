"""Run the three agreed questions once, preserving the original answers."""
from datetime import datetime

from answer_course import answer_question
from check_course_retrieval import QUESTIONS
from retrieval_core import ROOT, write_json
from search_course import CourseRetriever


def main():
    output = ROOT / 'data/processed/course_pilot/answers' / datetime.now().strftime('%Y%m%d-%H%M%S')
    output.mkdir(parents=True, exist_ok=False)
    retriever = CourseRetriever(ROOT / 'data/indexes/course_pilot')
    results = []
    for question in QUESTIONS:
        print(f"开始 {question['id']}：{question['query']}", flush=True)
        result = answer_question(question['query'], retriever=retriever, company=question['company'],
                                 output=output / f"{question['id']}.json")
        results.append({'question_id': question['id'], 'query': question['query'], 'status': result['status'],
                        'answer': result.get('answer'), 'error': result.get('error'),
                        'citation_check': result.get('citation_check'), 'usage': result.get('usage'),
                        'elapsed_seconds': result['elapsed_seconds'], 'answer_model': result.get('answer_model')})
        print(f"{question['id']}：{result['status']}；{result.get('answer') or result.get('error')}", flush=True)
    write_json(output / 'summary.json', {'created_at': datetime.now().astimezone().isoformat(),
                                        'mode': 'hybrid', 'top_k': 6, 'results': results})
    write_json(ROOT / 'data/processed/course_pilot/answers/latest_run.json', {'path': str(output)})
    print(f'三题记录已保存：{output}', flush=True)
    if any(row['status'] != 'completed' for row in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
