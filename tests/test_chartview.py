from jdmtool.chartview import ChartView

def test_code_to_key():
    # Known good codes.
    assert ChartView.code_to_key('023KM01684') == 73311856
    assert ChartView.code_to_key('023FL01687') == 73307503
    assert ChartView.code_to_key('050LG01704') == 203074450

    # Fake ones just to test the algorithm.
    assert ChartView.code_to_key('000AA00000') == 0
    assert ChartView.code_to_key('999ZZ99999') == 3898582511

    # Error case.
    try:
        ChartView.code_to_key('1234AB00001')
        assert False, "Expected ValueError for invalid code"
    except ValueError:
        pass
