"""Break down attendance residuals without changing the calibration score."""
import numpy as np


def residual_summary(actual, target):
    residual = actual - target
    expected = float(target.sum())
    observed = float(actual.sum())
    error = float(np.abs(residual).sum())
    return dict(expected_student_minutes=expected, actual_student_minutes=observed,
                absolute_error_student_minutes=error,
                signed_error_student_minutes=observed-expected,
                squared_error=float(np.square(residual).sum()),
                wape=error/expected if expected else None,
                actual_to_target=observed/expected if expected else None)


def attendance_breakdown(sim):
    # The calibration uses minute samples. Omit the closing boundary because
    # it represents no additional time in the simulated day.
    rows = sim.attendance[:-1]
    times = np.array([row['time'] for row in rows])
    buildings = sorted({name for row in rows for name in row['buildings']})
    indices = {building:index for index,building in enumerate(buildings)}
    actual = np.zeros((len(rows),len(buildings)))
    target = np.zeros_like(actual)
    for minute,row in enumerate(rows):
        for building,(observed,expected) in row['buildings'].items():
            actual[minute,indices[building]] = observed
            target[minute,indices[building]] = expected
    building_errors = [
        dict(building=building,**residual_summary(actual[:,i],target[:,i]))
        for i,building in enumerate(buildings)
    ]
    building_errors.sort(key=lambda row:row['absolute_error_student_minutes'],reverse=True)
    clock_errors = []
    for hour in range(6,24):
        selected = (times>=60*hour) & (times<60*(hour+1))
        clock_errors.append(dict(hour=hour,**residual_summary(actual[selected],target[selected])))

    starts = np.array([s.start for s in sim.sessions])
    ends = np.array([s.end for s in sim.sessions])
    sizes = np.array([s.target for s in sim.sessions])
    age = times[:,None]-starts
    active = (age>=0) & (times[:,None]<ends)
    session_target = active*sizes
    session_actual = np.asarray([row['sessions'] for row in rows])
    session_age_errors = []
    for start,end in ((0,5),(5,15),(15,30),(30,60),(60,np.inf)):
        selected = active & (age>=start) & (age<end)
        session_age_errors.append(dict(minutes_from_start=start,minutes_until=end if np.isfinite(end) else None,
                                       **residual_summary(session_actual[selected],session_target[selected])))
    session_size_errors = []
    for low,high in ((0,25),(25,75),(75,150),(150,np.inf)):
        selected = (sizes>=low) & (sizes<high)
        session_size_errors.append(dict(expected_attendees_min=low,
                                        expected_attendees_below=high if np.isfinite(high) else None,
                                        **residual_summary(session_actual[:,selected],session_target[:,selected])))
    return dict(overall=residual_summary(actual,target),
                time_only=residual_summary(actual.sum(axis=1),target.sum(axis=1)),
                building_only=residual_summary(actual.sum(axis=0),target.sum(axis=0)),
                buildings=building_errors,hours=clock_errors,
                session_age=session_age_errors,session_size=session_size_errors)
