"""
A simple script with a realistic bug to demo PuriPy's time-travel replay.
The function is supposed to find the highest-scoring student, but has an
off-by-one error in the grade calculation.
"""


def calculate_grade(score, total):
    percentage = score / total * 10  # bug: should be * 100
    if percentage >= 90:
        return "A"
    elif percentage >= 80:
        return "B"
    elif percentage >= 70:
        return "C"
    else:
        return "F"


def find_top_student(students):
    top_name = None
    top_score = -1
    for name, score in students.items():
        if score > top_score:
            top_score = score
            top_name = name
    return top_name, top_score


def main():
    students = {
        "Alice": 92,
        "Bob": 85,
        "Carol": 78,
        "Dave": 95,
    }

    results = {}
    for name, score in students.items():
        grade = calculate_grade(score, 100)
        results[name] = {"score": score, "grade": grade}

    top_name, top_score = find_top_student(students)
    top_grade = results[top_name]["grade"]

    print(f"Results: {results}")
    print(f"Top student: {top_name} with score {top_score} (grade: {top_grade})")
    print(f"Expected grade for {top_score}: A, got: {top_grade}")


if __name__ == "__main__":
    main()
