# -*- coding: utf-8 -*-
from django.shortcuts import get_object_or_404, render_to_response
from codespeed.models import Project, Revision, Result, Executable, Benchmark, Environment
from django.http import HttpResponse, Http404, HttpResponseNotAllowed, HttpResponseBadRequest, HttpResponseNotFound
from codespeed import settings
from datetime import datetime
from time import sleep
import json
from itertools import chain

def getbaselineexecutables():
    baseline = []
    if hasattr(settings, 'baselinelist') and settings.baselinelist != None:
        try:
            for entry in settings.baselinelist:
                executable = Executable.objects.get(id=entry['executable'])
                rev = Revision.objects.filter(
                    commitid=str(entry['revision']), project=executable.project
                )
                if len(rev) > 0:
                    rev = rev[0]
                else:
                    raise Revision.DoesNotExist
                name = executable.name
                if executable.coptions != "default" or executable.coptions != "none":
                    name += " " + executable.coptions
                if rev.tag: name += " " + rev.tag
                else: name += " " + rev.commitid
                baseline.append({
                    'executable': executable,
                    'revision': rev,
                    'name': name,
                })
        except (Executable.DoesNotExist, Revision.DoesNotExist):
            # TODO: write to server logs
            pass
    else:
        revs = Revision.objects.exclude(tag="")
        for rev in revs:
            #add executables that correspond to each tagged revision.
            executables = Executable.objects.filter(project=rev.project)
            for executable in executables:
                name = str(executable) + " " + rev.tag
                baseline.append({
                    'executable': executable,
                    'revision': rev,
                    'name': name,
                })
    # move default to first place
    if hasattr(settings, 'defaultbaseline') and settings.defaultbaseline != None:
        try:
            for base in baseline:
                if base['executable'] == settings.defaultbaseline['executable'] and base['revision'] == str(settings.defaultbaseline['revision']):
                    baseline.remove(base)
                    baseline.insert(0, base)
                    break
        except KeyError:
            # TODO: write to server logs
            #error in settings.defaultbaseline
            pass
    return baseline

def getdefaultenvironment():
    default = Environment.objects.all()
    if not len(default): return 0
    default = default[0]
    if hasattr(settings, 'defaultenvironment'):
        try:
            default = Environment.objects.get(name=settings.defaultenvironment)
        except Environment.DoesNotExist:
            pass
    return default

def getdefaultexecutable():
    default = None
    if hasattr(settings, 'defaultexecutable') and settings.defaultexecutable != None:
        try:
            default = Executable.objects.get(id=settings.defaultexecutable)
        except Executable.DoesNotExist:
            pass
    if default == None:
        execquery = Executable.objects.filter(project__track=True)
        if len(execquery): default = execquery[0]
    
    return default

def comparison(request):
    if request.method != 'GET': return HttpResponseNotAllowed('GET')
    data = request.GET
    
    # Configuration of default parameters
    defaultenvironment = getdefaultenvironment()
    if not defaultenvironment:
        return HttpResponse("You need to configure at least one Environment")
    if 'host' in data:
        try:
            defaultenvironment = Environment.objects.get(name=data['host'])
        except Environment.DoesNotExist:
            pass
    
    if not len(Project.objects.all()):
        return HttpResponse("You need to configure at least one Project as default")
    
    executables = []
    executablekeys = []
    # add all tagged revs for any project
    revisions = Revision.objects.exclude(tag="")
    for rev in revisions:
        for exe in Executable.objects.filter(project=rev.project):
            name = str(exe) + " " + rev.tag
            key = str(exe.id) + "+" + str(rev.id)
            executablekeys.append(key)
            executables.append({
                'key': key,
                'executable': exe,
                'revision': rev,
                'name': name,
            })
    
    # add latest revs of tracked projects
    projects = Project.objects.filter(track=True)
    for proj in projects:
        rev = Revision.objects.filter(project=proj).latest('date')
        if rev not in revisions:
            for exe in Executable.objects.filter(project=rev.project):
                name = str(exe) + " latest"
                key = str(exe.id) + "+L"
                executablekeys.append(key)
                executables.append({
                    'key': key,
                    'executable': exe,
                    'revision': rev,
                    'name': name,
                })
    
    checkedexecutables = []
    k = executablekeys
    if 'exe' in data:
        for i in data['exe'].split(","):
            if not i: continue
            if i in k:
                checkedexecutables.append(i)
    if not checkedexecutables:
        checkedexecutables = k
    
    benchmarks = Benchmark.objects.all()
    checkedbenchmarks = []
    if 'ben' in data:
        checkedbenchmarks = []
        for i in data['ben'].split(","):
            if not i: continue
            try:
                checkedbenchmarks.append(Benchmark.objects.get(id=int(i)))
            except Benchmark.DoesNotExist:
                pass
    if not checkedbenchmarks:
        checkedbenchmarks = benchmarks
    
    hosts = Environment.objects.all()
    checkedhosts = []
    if 'env' in data:
        for i in data['env'].split(","):
            if not i: continue
            try:
                checkedhosts.append(Environment.objects.get(id=int(i)))
            except Environment.DoesNotExist:
                pass
    if not checkedhosts:
        checkedhosts = hosts
    
    charts = ['bars', 'stacked bars']
    selectedchart = charts[0]
    if 'chart' in data and data['chart'] in charts:
        selectedchart = data['chart']
    
    return render_to_response('codespeed/comparison.html', {
        'checkedexecutables': checkedexecutables,
        'checkedbenchmarks': checkedbenchmarks,
        'checkedhosts': checkedhosts,
        'defaultenvironment': defaultenvironment,
        'executables': executables,
        'benchmarks': benchmarks,
        'hosts': hosts,
        'charts': charts,
        'selectedchart': selectedchart
    })

def gettimelinedata(request):
    if request.method != 'GET': return HttpResponseNotAllowed('GET')
    data = request.GET
    
    timeline_list = {'error': 'None', 'timelines': []}
    executables = data['exe'].split(",")
    if executables[0] == "":
        timeline_list['error'] = "No executables selected"
        return HttpResponse(json.dumps( timeline_list ))

    environment = Environment.objects.get(name=data['host'])
    benchmarks = []
    number_of_rev = data['revs']
    if data['ben'] == 'grid':
        benchmarks = Benchmark.objects.all().order_by('name')
        number_of_rev = 15
    else:
        benchmarks.append(Benchmark.objects.get(name=data['ben']))
    
    baseline = getbaselineexecutables()
    if len(baseline): baseline = baseline[0]
    else: baseline = None
    baselinerev = None
    if data['base'] == "true":
        baselinerev = baseline['revision']
    for bench in benchmarks:
        append = False
        timeline = {}
        timeline['benchmark'] = bench.name
        timeline['units'] = bench.units
        lessisbetter = bench.lessisbetter and ' (less is better)' or ' (more is better)'
        timeline['lessisbetter'] = lessisbetter
        timeline['executables'] = {}
        
        for executable in executables:
            resultquery = Result.objects.filter(
                    benchmark=bench
                ).filter(
                    environment=environment
                ).filter(
                    executable=executable
                ).order_by('-revision__date')[:number_of_rev]
            if not len(resultquery): continue
            results = []
            for res in resultquery:
                std_dev = ""
                if res.std_dev != None: std_dev = res.std_dev
                results.append(
                    [str(res.revision.date), res.value, std_dev, res.revision.commitid]
                )
            timeline['executables'][executable] = results
            append = True
        if data['base'] == "true" and baseline != None and append:
            try:
                baselinevalue = Result.objects.get(
                    executable=baseline['executable'],
                    benchmark=bench,
                    revision=baselinerev,
                    environment=environment
                ).value
            except Result.DoesNotExist:
                timeline['baseline'] = "None"
            else:
                # determine start and end revision (x axis) from longest data series
                results = []
                for exe in timeline['executables']:
                    if len(timeline['executables'][exe]) > len(results):
                        results = timeline['executables'][exe]
                end = results[0][0]
                start = results[len(results)-1][0]
                timeline['baseline'] = [
                    [str(start), baselinevalue],
                    [str(end), baselinevalue]
                ]
        if append: timeline_list['timelines'].append(timeline)
    
    if not len(timeline_list['timelines']):
        response = 'No data found for the selected options'
        timeline_list['error'] = response
    return HttpResponse(json.dumps( timeline_list ))

def timeline(request):
    if request.method != 'GET': return HttpResponseNotAllowed('GET')
    data = request.GET
    
    # Configuration of default parameters
    defaultenvironment = getdefaultenvironment()
    if not defaultenvironment:
        return HttpResponse("You need to configure at least one Environment")
    if 'host' in data:
        try:
            defaultenvironment = Environment.objects.get(name=data['host'])
        except Environment.DoesNotExist:
            pass
    
    defaultproject = Project.objects.filter(track=True)
    if not len(defaultproject):
        return HttpResponse("You need to configure at least one Project as default")
    else: defaultproject = defaultproject[0]
    
    baseline = getbaselineexecutables()
    
    defaultbaseline = True
    if 'base' in data and data['base'] == "false":
        defaultbaseline = False
    if len(baseline): baseline = baseline[0]
    else: defaultbaseline = False
    
    defaultbenchmark = "grid"
    if 'ben' in data and data['ben'] != defaultbenchmark:
        defaultbenchmark = get_object_or_404(Benchmark, name=data['ben'])
    
    checkedexecutables = []
    if 'exe' in data:
        for i in data['exe'].split(","):
            if not i: continue
            selected = Executable.objects.filter(id=int(i))
            if len(selected): checkedexecutables.append(selected[0])
    if not checkedexecutables:
        checkedexecutables = Executable.objects.filter(project__track=True)
    
    lastrevisions = [10, 50, 200, 1000]
    defaultlast = 200
    if 'revs' in data:
        if int(data['revs']) not in lastrevisions:
            lastrevisions.append(data['revs'])
        defaultlast = data['revs']
    
    # Information for template
    executables = Executable.objects.filter(project__track=True)
    benchmarks = Benchmark.objects.all()
    hostlist = Environment.objects.all()
    return render_to_response('codespeed/timeline.html', {
        'checkedexecutables': checkedexecutables,
        'defaultbaseline': defaultbaseline,
        'baseline': baseline,
        'defaultbenchmark': defaultbenchmark,
        'defaultenvironment': defaultenvironment,
        'lastrevisions': lastrevisions,
        'defaultlast': defaultlast,
        'executables': executables,
        'benchmarks': benchmarks,
        'hostlist': hostlist
    })

def getoverviewtable(request):
    data = request.GET
    
    executable = Executable.objects.get(id=int(data['exe']))
    environment = Environment.objects.get(name=data['host'])
    trendconfig = int(data['tre'])
    selectedrev = Revision.objects.get(
        commitid=data['rev'], project=executable.project
    )
    date = selectedrev.date
    lastrevisions = Revision.objects.filter(
        project=executable.project
    ).filter(
        date__lte=date
    ).order_by('-date')[:trendconfig+1]
    lastrevision = lastrevisions[0]

    change_list = []
    pastrevisions = []
    if len(lastrevisions) > 1:
        changerevision = lastrevisions[1]
        change_list = Result.objects.filter(
            revision=changerevision
        ).filter(
            environment=environment
        ).filter(
            executable=executable
        )
        pastrevisions = lastrevisions[trendconfig-2:trendconfig+1]

    result_list = Result.objects.filter(
        revision=lastrevision
    ).filter(
        environment=environment
    ).filter(
        executable=executable
    )
    
    base_list = None
    baseexecutable = None
    if "baseline" in data and data['baseline'] != "undefined":
        tulp = data['baseline'].split("+")
        exe = Executable.objects.get(id=tulp[0])
        rev = Revision.objects.get(id=tulp[1])
        base_list = Result.objects.filter(
            revision=rev
        ).filter(
            environment=environment
        ).filter(
            executable=exe
        )

    table_list = []
    totals = {'change': [], 'trend': [],}
    for bench in Benchmark.objects.all():
        resultquery = result_list.filter(benchmark=bench)
        if not len(resultquery): continue
        result = resultquery.filter(benchmark=bench)[0]
        std_dev = result.std_dev
        result = result.value
        
        change = 0
        if len(change_list):
            c = change_list.filter(benchmark=bench)
            if c.count():
                change = (result - c[0].value)*100/c[0].value
                totals['change'].append(result / c[0].value)
        
        #calculate past average
        average = 0
        averagecount = 0
        if len(pastrevisions):
            for rev in pastrevisions:
                past_rev = Result.objects.filter(
                    revision=rev
                ).filter(
                    environment=environment
                ).filter(
                    executable=executable
                ).filter(benchmark=bench)
                if past_rev.count():
                    average += past_rev[0].value
                    averagecount += 1
        trend = 0
        if average:
            average = average / averagecount
            trend =  (result - average)*100/average
            totals['trend'].append(result / average)
        else:
            trend = "-"

        relative = 0
        if base_list:
            c = base_list.filter(benchmark=bench)
            if c.count():
                relative =  c[0].value / result
                #totals['relative'].append(relative)#deactivate average for comparison
        table_list.append({
            'benchmark': bench,
            'result': result,
            'std_dev': std_dev,
            'change': change,
            'trend': trend,
            'relative': relative,
        })
    
    if not len(table_list):
        return HttpResponse('<table id="results" class="tablesorter" style="height: 232px;"></table><p>No results for this parameters</p>')
    # Compute Arithmetic averages
    for key in totals.keys():
        if len(totals[key]):
            totals[key] = float(sum(totals[key]) / len(totals[key]))
        else:
            totals[key] = "-"
    if totals['change'] != "-":
        totals['change'] = (totals['change'] - 1) * 100#transform ratio to percentage
    if totals['trend'] != "-":
        totals['trend'] = (totals['trend'] - 1) * 100#transform ratio to percentage
    
    # Only show units column if a benchmark has units other than seconds
    showunits = False
    if len(Benchmark.objects.exclude(units='seconds')): showunits = True
    
    return render_to_response('codespeed/overview_table.html', {
        'table_list': table_list,
        'baseexecutable': baseexecutable,
        'trendconfig': trendconfig,
        'showunits': showunits,
        'showcomparison': base_list,
        'executable': executable,
        'lastrevision': lastrevision,
        'totals': totals
    })
    
def overview(request):
    if request.method != 'GET': return HttpResponseNotAllowed('GET')
    data = request.GET

    # Configuration of default parameters
    defaultenvironment = getdefaultenvironment()
    if not defaultenvironment:
        return HttpResponse("You need to configure at least one Environment")
    if 'host' in data:
        try:
            defaultenvironment = Environment.objects.get(name=data['host'])
        except Environment.DoesNotExist:
            pass
    
    defaultchangethres = 3
    defaulttrendthres = 3
    defaulttrend = 10
    trends = [5, 10, 20, 50, 100]
    if 'tre' in data and int(data['tre']) in trends:
        defaulttrend = int(data['tre'])

    defaultexecutable = getdefaultexecutable()
    if not defaultexecutable:
        return HttpResponse("You need to configure at least one Project as default")
    
    if "exe" in data:
        try:
            defaultexecutable = Executable.objects.get(id=int(data['exe']))
        except Executable.DoesNotExist:
            pass
        except ValueError:
            pass
    baseline = getbaselineexecutables()
    defaultbaseline = None
    if len(baseline):
        defaultbaseline = str(baseline[0]['executable'].id) + "+"
        defaultbaseline += str(baseline[0]['revision'].id)
    if "base" in data and data['base'] != "undefined":
        try:
            defaultbaseline = request.GET['base']
        except ValueError:
            pass
    
    # Information for template
    executables = Executable.objects.filter(project__track=True)
    revlimit = 20
    lastrevisions = Revision.objects.filter(
        project=defaultexecutable.project
    ).order_by('-date')[:revlimit]
    if not len(lastrevisions):
        response = 'No data found for project "' + str(defaultexecutable.project) + '"'
        return HttpResponse(response)
    selectedrevision = lastrevisions[0]
    if "rev" in data:
        commitid = data['rev']
        try:
            selectedrevision = Revision.objects.get(
                commitid=commitid, project=defaultexecutable.project
            )
            if not selectedrevision in lastrevisions:
                lastrevisions = list(chain(lastrevisions))
                lastrevisions.append(selectedrevision)
        except Revision.DoesNotExist:
            selectedrevision = lastrevisions[0]
            
    hostlist = Environment.objects.all()
    projectmatrix = {}
    for e in executables: projectmatrix[e.id] = e.project.name
    projectmatrix = json.dumps(projectmatrix)
    projectlist = []
    for p in Project.objects.filter(
            track=True
        ).exclude(
            id=defaultexecutable.project.id
        ):
        projectlist.append(p)
    revisionboxes = { defaultexecutable.project.name: lastrevisions }
    for p in projectlist:
        revisionboxes[p.name] = Revision.objects.filter(
            project=p
        ).order_by('-date')[:revlimit]
    return render_to_response('codespeed/overview.html', locals())

def displaylogs(request):
    rev = Revision.objects.get(id=request.GET['revisionid'])
    logs = []
    logs.append(rev)
    remotelogs = getcommitlogs(rev)
    if len(remotelogs): logs = remotelogs
    return render_to_response('codespeed/overview_logs.html', { 'logs': logs })

def getlogsfromsvn(newrev, startrev):
    import pysvn
    logs = []
    loglimit = 200
    if startrev == newrev:
        start = startrev.commitid
    else:
        #don't show info corresponding to previously tested revision
        start = int(startrev.commitid) + 1
    
    def get_login(realm, username, may_save):
        return True, newrev.project.repo_user, newrev.project.repo_pass, False
    
    client = pysvn.Client()
    if newrev.project.repo_user != "":
        client.callback_get_login = get_login
    log_message = \
        client.log(
            newrev.project.repo_path,
            revision_start=pysvn.Revision(
                    pysvn.opt_revision_kind.number, start
            ),
            revision_end=pysvn.Revision(
                pysvn.opt_revision_kind.number, newrev.commitid
            )
        )
    log_message.reverse()
    s = len(log_message)
    while s > loglimit:
        log_message = log_message[:s]
        s = len(log_message) - 1
    for log in log_message:
        try:
            author = log.author
        except AttributeError:
            author = ""
        date = datetime.fromtimestamp(log.date).strftime("%Y-%m-%d %H:%M:%S")
        message = log.message
        logs.append({'date': date, 'author': author, 'message': message, 'commitid': log.revision.number})
    return logs

def getcommitlogs(rev):
    logs = []
    if rev.project.repo_type == 'N' or rev.project.repo_path == "":
        #Don't create logs
        return []
    
    startrev = Revision.objects.filter(
        project=rev.project
    ).filter(date__lt=rev.date).order_by('-date')[:1]
    if not len(startrev): startrev = rev
    else: startrev = startrev[0]
    
    if rev.project.repo_type == 'S':
        logs = getlogsfromsvn(rev, startrev)
    return logs

def saverevisioninfo(rev):
    log = None
    if rev.project.repo_type == 'N' or rev.project.repo_path == "":
        #Don't create logs
        return
    elif rev.project.repo_type == 'S':
        log = getlogsfromsvn(rev, rev)
    if len(log):
        log = log[0]
        rev.author  = log['author']
        rev.date    = log['date']
        rev.message = log['message']
    else:
        rev.date = datetime.now()

def addresult(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed('POST')
    data = request.POST
    
    mandatory_data = [
        'commitid',
        'project',
        'executable_name',
        'benchmark',
        'environment',
        'result_value',
    ]
    
    for key in mandatory_data:
        if not key in data:
            return HttpResponseBadRequest('Key "' + key + '" missing from request')
        elif key in data and data[key] == "":
            return HttpResponseBadRequest('Key "' + key + '" empty in request')

    # Check that Environment exists
    try:
        e = get_object_or_404(Environment, name=data['environment'])
    except Http404:
        return HttpResponseNotFound("Environment " + data["environment"] + " not found")
    
    p, created = Project.objects.get_or_create(name=data["project"])
    b, created = Benchmark.objects.get_or_create(name=data["benchmark"])
    
    rev, created = Revision.objects.get_or_create(
        commitid=data['commitid'],
        project=p,
    )
    if created:
        if 'revision_date' in data: rev.date = data["revision_date"]
        else:
            try:
                saverevisioninfo(rev)
            except:
                pass
        if not rev.date:
            temp = datetime.today()
            rev.date = datetime(temp.year, temp.month, temp.day, temp.hour, temp.minute, temp.second)

        rev.save()
    
    coptions = ""
    if 'executable_coptions' in data: coptions = data['executable_coptions']
    exe, created = Executable.objects.get_or_create(
        name=data['executable_name'],
        coptions=coptions,
        project=p
    )
    
    try:
        r = Result.objects.get(revision=rev,executable=exe,benchmark=b,environment=e)
    except Result.DoesNotExist:
        r = Result(revision=rev,executable=exe,benchmark=b,environment=e)
    r.value = data["result_value"]    
    if 'result_date' in data: r.date = data["result_date"]
    else: r.date = rev.date
    if 'std_dev' in data: r.std_dev = data['std_dev']
    if 'min' in data: r.val_min = data['min']
    if 'max' in data: r.val_max = data['max']
    r.save()
    
    return HttpResponse("Result data saved succesfully")
