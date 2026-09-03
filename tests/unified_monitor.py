import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
PH_TZ=timezone(timedelta(hours=8)); FLOOD_STATE=Path('flood_state.json'); PAGASA_STATE=Path('pagasa_state.json'); RADAR_STATE=Path('radar_threat_state.json'); ASSESSMENT_STATE=Path('assessment_state.json'); MONITOR_STATE=Path('monitor_notification_state.json'); ALERT_FILE=Path('combined_alert.txt'); ESCALATION_FILE=Path('escalation_alert.txt')
POINTS=[('P1','Pananalig × Villarica','LOCAL'),('P2','Villarica × Nanirahan','LOCAL'),('P3','J.P. Rizal × San Pedro','ESCAPE ROUTE'),('P4','J.P. Rizal × Ilino Cruz','ESCAPE ROUTE'),('P5','J.P. Rizal × Saniboy','ESCAPE ROUTE'),('P6','J.P. Rizal × Coronado','ESCAPE ROUTE')]

def load(p,d):
    try:return json.loads(p.read_text(encoding='utf-8'))
    except:return d
def warning_description(l):return {'GREEN':'No active PAGASA Heavy Rainfall Warning for Metro Manila','YELLOW':'Possible flooding in flood-prone areas','ORANGE':'Flooding is threatening','RED':'Severe flooding is expected'}.get(l,'PAGASA warning status unavailable')
def warning_icon(l):return {'GREEN':'🟢','YELLOW':'🟡','ORANGE':'🟠','RED':'🔴'}.get(l,'⚪')
def rank(l):return {'Low':1,'Medium':2,'High':3}.get(l,0)
def radar_candidates(r):
    if r.get('status')!='ok':return []
    return [x for x in r.get('top_threats',[]) if x.get('threat_candidate') and x.get('threat_score',0)>=55 and x.get('distance_to_mandaluyong_km',9999)<=50 and x.get('pixels_current',0)>=150 and (x.get('current_class') in {'yellow','orange','red','purple'} or x.get('previous_class') in {'yellow','orange','red','purple'})]
def flood_summary(f):
    pts=[p for p in f.values() if isinstance(p,dict)]; high=[p for p in pts if p.get('risk_level')=='High']; med=[p for p in pts if p.get('risk_level')=='Medium']; esc=[p for p in pts if p.get('purpose')=='ESCAPE ROUTE']; elev=[p for p in esc if p.get('risk_level') in ('Medium','High')]; status='HIGH RISK' if high else 'ESCAPE ROUTE ELEVATED' if len(elev)>=2 else 'WATCH' if med else 'NO DATA' if not pts else 'NORMAL'; rain=[p.get('rainfall') for p in pts if isinstance(p.get('rainfall'),(int,float))]; return status,max(rain) if rain else None,elev

def build_messages():
    pagasa=load(PAGASA_STATE,{}); flood=load(FLOOD_STATE,{}); radar=load(RADAR_STATE,{}); assess=load(ASSESSMENT_STATE,{}); previous=load(MONITOR_STATE,{}); level=pagasa.get('level','UNKNOWN'); flood_status,rainfall,elevated_escape=flood_summary(flood); candidates=radar_candidates(radar); now=datetime.now(PH_TZ); lines=[f'{warning_icon(level)} PAGASA NCR WEATHER STATUS','',f'Warning Level: {level}',warning_description(level),'',f'Checked: {now.strftime("%Y-%m-%d %I:%M %p")}','','MANDALUYONG LOCAL MONITORING','',f'Flood status: {flood_status}']
    if rainfall is not None:lines.append(f'Rainfall: {rainfall} mm')
    lines+=['','Monitoring points:']
    for pid,name,purpose in POINTS:
        p=flood.get(pid)
        if not p:lines.append(f'⚪ {pid} {name} — NO DATA');continue
        r=p.get('risk_level','Unknown'); icon={'High':'🔴','Medium':'🟡','Low':'🟢'}.get(r,'⚪');lines.append(f'{icon} {pid} {name} — {r} ({p.get("risk_score",0)})')
    lines.append(''); lines.append(f'⚠️ Escape route: {len(elevated_escape)} point(s) elevated' if elevated_escape else '🛣️ Escape route: CLEAR')
    lines+=['','Radar monitoring:']
    if candidates:
        lines.append(f'🔴 {len(candidates)} qualifying radar threat(s) near/approaching Mandaluyong')
        for x in sorted(candidates,key=lambda x:-x.get('threat_score',0))[:3]:lines.append(f'   • {x.get("current_class","unknown").upper()} | score {x.get("threat_score",0):.0f} | {x.get("distance_to_mandaluyong_km",0):.1f} km | movement {x.get("movement_km",0):.1f} km')
    elif radar.get('status')=='ok':lines.append('🟢 No qualifying radar core detected for Mandaluyong')
    else:lines.append('⚪ Radar status unavailable')
    pred=assess.get('prediction',{})
    if pred:
        icon={'HIGH':'🔴','WATCH':'🟠','LOW-MODERATE':'🟡','LOW':'🟢','UNKNOWN':'⚪'}.get(pred.get('risk'),'⚪'); lines+=['',f'{icon} 3-DAY FLOOD ASSESSMENT',f'Prediction: {pred.get("risk","UNKNOWN")}',f'Risk score: {pred.get("score",0)}/100',f'Confidence: {pred.get("confidence",0):.0%}',f'Window: {pred.get("window","latest 3 days")}']; sig=', '.join(pred.get('signals',[])) or 'No significant precursor signals'; lines+=['',f'Signals: {sig}',f'Assessment: {pred.get("interpretation","")}']
    ALERT_FILE.write_text('\n'.join(lines),encoding='utf-8'); previous_level=previous.get('pagasa_level','UNKNOWN'); previous_flood=previous.get('flood',{}); previous_radar=previous.get('radar_candidate',False); escalations=[]; order={'UNKNOWN':0,'GREEN':1,'YELLOW':2,'ORANGE':3,'RED':4}
    if order.get(level,0)>order.get(previous_level,0) and previous_level!='UNKNOWN':escalations.append(f'PAGASA warning level increased: {previous_level} → {level}')
    for pid,name,_ in POINTS:
        old=previous_flood.get(pid,{}); new=flood.get(pid,{})
        if rank(new.get('risk_level'))>rank(old.get('risk_level')) and old.get('risk_level'):escalations.append(f'{pid} {name}: {old.get("risk_level")} → {new.get("risk_level")}')
    if elevated_escape and not previous.get('escape_elevated',False):escalations.append('Escape route risk became elevated')
    if candidates and not previous_radar:escalations.append('Qualifying radar threat detected near/approaching Mandaluyong')
    ESCALATION_FILE.unlink(missing_ok=True)
    if escalations:ESCALATION_FILE.write_text('🚨 MANDALUYONG MONITOR ESCALATION\n\n'+'\n'.join(f'• {x}' for x in escalations)+f'\n\nChecked: {now.strftime("%Y-%m-%d %I:%M %p")}',encoding='utf-8')
    MONITOR_STATE.write_text(json.dumps({'pagasa_level':level,'flood':flood,'radar_candidate':bool(candidates),'escape_elevated':bool(elevated_escape),'last_checked':now.isoformat()},indent=2),encoding='utf-8');print('\n'.join(lines))
if __name__=='__main__':build_messages()
