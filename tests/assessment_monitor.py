import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
PH_TZ=timezone(timedelta(hours=8)); STATE_FILE=Path('assessment_history.json'); OUTPUT_FILE=Path('assessment_state.json'); LOG_FILE=Path('assessment_log.json'); ALERT_FILE=Path('assessment_alert.txt')
POINTS=['P1','P2','P3','P4','P5','P6']; UNKNOWN={'UNKNOWN','UNAVAILABLE','NONE','NULL',''}
def ts(v): return datetime.strptime(v,'%Y-%m-%d %H:%M:%S').replace(tzinfo=PH_TZ)
def num(v):
    try:return float(v)
    except:return 0.0
def load(p,d):
    try:return json.loads(p.read_text(encoding='utf-8'))
    except:return d
def snapshot():
    flood=load(Path('flood_state.json'),{}); pagasa=load(Path('pagasa_state.json'),{}); radar=load(Path('radar_state.json'),{}); points={}; times=[]; rains=[]
    for p in POINTS:
        r=flood.get(p,{}); points[p]={'risk_level':r.get('risk_level'),'risk_score':num(r.get('risk_score')),'rainfall_mm':num(r.get('rainfall')),'timestamp':r.get('timestamp')}
        if r.get('timestamp'):
            try:times.append(ts(r['timestamp']))
            except ValueError:pass
        rains.append(points[p]['rainfall_mm'])
    return {'timestamp':max(times).strftime('%Y-%m-%d %H:%M:%S') if times else None,'rainfall_mm':max(rains) if rains else 0.0,'points':points,'pagasa_warning':str(pagasa.get('level') or pagasa.get('warning_level') or 'UNKNOWN').upper(),'radar_status':str(radar.get('status') or radar.get('radar_status') or 'UNKNOWN').upper(),'flood_status':'ELEVATED' if any(v['risk_level'] not in (None,'Low','LOW') for v in points.values()) else 'NORMAL'}
def assess(history):
    parsed=[]
    for r in history:
        try:parsed.append((ts(r['timestamp']),r))
        except (KeyError,TypeError,ValueError):pass
    parsed.sort(key=lambda x:x[0]); now=parsed[-1][0]; cutoff=now-timedelta(days=3); rows=[r for t,r in parsed if t>=cutoff]; days={t.date() for t,_ in parsed if t>=cutoff}; confidence=.90 if len(days)>=3 and len(rows)>=3 else (.75 if len(days)>=2 and len(rows)>=3 else .45); score=0.; signals=[]; rain=[num(r.get('rainfall_mm')) for r in rows]
    if rain:
        m=max(rain)
        if m>=10:score+=35;signals.append('high rainfall pulse')
        elif m>=5:score+=20;signals.append('moderate rainfall pulse')
        elif m>=2:score+=8;signals.append('rainfall pulse')
    latest=rows[-1]; ps=[num(v.get('risk_score')) for v in latest.get('points',{}).values()]
    max_level=max(ps) if ps else 0.0; mean_level=sum(ps)/len(ps) if ps else 0.0
    if ps:
        high=sum(x>=2 for x in ps); med=sum(x>=1 for x in ps)
        if max_level>=3:score+=40;signals.append('high monitoring-point level')
        elif max_level>=2:score+=25;signals.append('elevated monitoring-point level')
        elif max_level>=1:score+=10;signals.append('monitoring-point pressure')
        if high>=2:score+=15;signals.append('multi-point escalation')
        elif med>=3:score+=8;signals.append('multi-point pressure')
    rises=0
    for a,b in zip(rows,rows[1:]):
        aa=sum(num(v.get('risk_score')) for v in a.get('points',{}).values())/6;bb=sum(num(v.get('risk_score')) for v in b.get('points',{}).values())/6
        if bb>aa+.15:rises+=1
    if rises>=3:score+=25;signals.append('persistent water-level rise')
    elif rises==2:score+=12;signals.append('repeated water-level rise')
    # Pre-flood pressure: sustained elevated levels matter even when no point is yet Medium/High.
    pressure_rows=[]
    for r in rows:
        vals=[num(v.get('risk_score')) for v in r.get('points',{}).values()]
        if vals and max(vals)>=1.0 and sum(x>=1.0 for x in vals)>=3:pressure_rows.append(r)
    pressure_count=len(pressure_rows)
    if pressure_count>=3:
        score+=20;signals.append('sustained multi-point pressure')
    elif pressure_count==2:
        score+=10;signals.append('developing multi-point pressure')
    # Detect the validated pattern: elevated levels persist, then rainfall/warning forcing occurs.
    pressure_rising=False
    if len(pressure_rows)>=2:
        first=pressure_rows[0]; last=pressure_rows[-1]
        fa=sum(num(v.get('risk_score')) for v in first.get('points',{}).values())/6
        la=sum(num(v.get('risk_score')) for v in last.get('points',{}).values())/6
        pressure_rising=la>fa+.15
        if pressure_rising:
            score+=10;signals.append('pressure is still rising')
    if latest.get('pagasa_warning')=='YELLOW':score+=10;signals.append('PAGASA yellow')
    elif latest.get('pagasa_warning')=='ORANGE':score+=20;signals.append('PAGASA orange')
    elif latest.get('pagasa_warning')=='RED':score+=30;signals.append('PAGASA red')
    if latest.get('radar_status') in UNKNOWN:confidence-=.05;signals.append('radar unavailable; confidence slightly reduced')
    score=max(0,min(100,score)); risk='HIGH' if score>=65 else 'WATCH' if score>=35 else 'LOW-MODERATE' if score>=15 else 'LOW'
    # Separate operational flag: imminent means the validated precursor pattern is present, not merely a high score.
    imminent=(pressure_count>=3 and (pressure_rising or latest.get('pagasa_warning') in ('ORANGE','RED')) and max_level>=1.0)
    if imminent:signals.append('IMMINENT WINDOW: sustained pressure plus forcing')
    confidence=max(.2,min(.95,confidence))
    return {'risk':risk,'score':round(score,1),'confidence':round(confidence,2),'window':'latest 3 days','imminent':imminent,'lead_signal':'Pre-flood pressure detected; elevated water levels are persisting and/or rising under rainfall/warning forcing.' if imminent else 'No validated imminent pattern yet.','signals':signals,'interpretation':'The validated precursor pattern is accumulation/persistence first, followed by renewed rainfall or warning forcing. Sustained multi-point pressure is more important than a single rainfall pulse; isolated pulses that recover should not trigger an imminent prediction.','data_quality':{'distinct_days':len(days),'rows_in_window':len(rows),'radar_available':latest.get('radar_status') not in UNKNOWN}}
def main():
    cur=snapshot()
    if not cur['timestamp']:raise SystemExit('flood_state.json has no usable timestamp')
    history=load(STATE_FILE,[])
    if history and history[-1].get('timestamp')==cur['timestamp']:history[-1]=cur
    else:history.append(cur)
    history=history[-5000:];STATE_FILE.write_text(json.dumps(history,ensure_ascii=False,indent=2),encoding='utf-8');pred=assess(history);out={'schema_version':3,'generated_at':cur['timestamp'],'model':'3-day flood precursor assessment','prediction':pred,'current_snapshot':cur,'data_quality':pred['data_quality']};OUTPUT_FILE.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');log=load(LOG_FILE,{});log['latest_monitor_run']=out;LOG_FILE.write_text(json.dumps(log,ensure_ascii=False,indent=2),encoding='utf-8');icon={'HIGH':'🔴','WATCH':'🟠','LOW-MODERATE':'🟡','LOW':'🟢','UNKNOWN':'⚪'}[pred['risk']];imminent='YES' if pred['imminent'] else 'NO';sig=', '.join(pred['signals']) or 'No significant precursor signals';ALERT_FILE.write_text(f"{icon} MANDALUYONG FLOOD ASSESSMENT\n\nPrediction: {pred['risk']}\nRisk score: {pred['score']}/100\nConfidence: {pred['confidence']:.0%}\nImminent window: {imminent}\nWindow: latest 3 days\n\nCurrent flood status: {cur['flood_status']}\nPAGASA: {cur['pagasa_warning']}\nRadar: {cur['radar_status']}\n\nSignals:\n{sig}\n\nAssessment: {pred['interpretation']}\n",encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
