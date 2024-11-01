#!/bin/sh
set -xue

while getopts "o" option;
do
 case $option in
  o)
   echo "Received -o flag for optional checkout of GTG, will check out GTG with EMC_post"
   checkout_gtg="YES"
   checkout_wafs="YES"
   gtg_git_args="--recursive"
   ;;
  :)
   echo "option -$OPTARG needs an argument"
   ;;
  *)
   echo "invalid option -$OPTARG, exiting..."
   exit
   ;;
 esac
done

topdir=$(pwd)
echo $topdir

echo fv3gfs checkout ...
if [[ ! -d fv3gfs.fd ]] ; then
    rm -f ${topdir}/checkout-fv3gfs.log
    git clone --recursive --branch GFS.v16.3.1 https://github.com/ufs-community/ufs-weather-model.git fv3gfs.fd >> ${topdir}/checkout-fv3gfs.log 2>&1
    cd ${topdir}
else
    echo 'Skip.  Directory fv3gfs.fd already exists.'
fi

echo gsi checkout ...
if [[ ! -d gsi.fd ]] ; then
    rm -f ${topdir}/checkout-gsi.log
#    git clone --recursive --branch gfsda.v16.3.12 https://github.com/NOAA-EMC/GSI.git gsi.fd >> ${topdir}/checkout-gsi.log 2>&1
# Check out develop for now
    git clone --recursive https://github.com/NOAA-EMC/GSI.git gsi.fd >> ${topdir}/checkout-gsi.log 2>&1
    cd gsi.fd
    git submodule update --init
    cd ${topdir}
else
    echo 'Skip.  Directory gsi.fd already exists.'
fi

echo gsi_utils checkout ...
if [[ ! -d gsi_utils.fd ]] ; then
    rm -f ${topdir}/checkout-gsi_utils.log
# Check out a version before the changes for Thompson microphysics were introduced.
    git clone https://github.com/NOAA-EMC/GSI-Utils.git gsi_utils.fd >> ${topdir}/checkout-gsi_utils.log 2>&1
    cd gsi_utils.fd
    git checkout 2a15d3b514cb05a9c1343e437f134375ad260369
    cd ${topdir}
else
    echo 'Skip.  Directory gsi_utils.fd already exists.'
fi

echo gsi_monitor checkout ...
if [[ ! -d gsi_monitor.fd ]] ; then
    rm -f ${topdir}/checkout-gsi_monitor.log
# Check out a version before the changes for Thompson microphysics were introduced.
    git clone https://github.com/NOAA-EMC/GSI-Monitor.git gsi_monitor.fd >> ${topdir}/checkout-gsi_monitor.log 2>&1
    cd gsi_monitor.fd
    git checkout 94588d63ca636269474bf865603e0ccfeb4dc049
    cd ${topdir}
else
    echo 'Skip.  Directory gsi_monitor.fd already exists.'
fi


echo gldas checkout ...
if [[ ! -d gldas.fd ]] ; then
    rm -f ${topdir}/checkout-gldas.log
    git clone --branch gldas_gfsv16_release.v.2.1.0 https://github.com/NOAA-EMC/GLDAS  gldas.fd >> ${topdir}/checkout-gldas.fd.log 2>&1
    cd ${topdir}
else
    echo 'Skip.  Directory gldas.fd already exists.'
fi

echo ufs_utils checkout ...
if [[ ! -d ufs_utils.fd ]] ; then
    rm -f ${topdir}/checkout-ufs_utils.log
    git clone --branch ops-gfsv16.3.0 https://github.com/ufs-community/UFS_UTILS ufs_utils.fd >> ${topdir}/checkout-ufs_utils.fd.log 2>&1
    cd ${topdir}
else
    echo 'Skip.  Directory ufs_utils.fd already exists.'
fi

echo EMC_post checkout ...
if [[ ! -d gfs_post.fd ]] ; then
    rm -f ${topdir}/checkout-gfs_post.log
    git clone ${gtg_git_args:-} --branch upp_v8.3.0 https://github.com/NOAA-EMC/UPP.git gfs_post.fd >> ${topdir}/checkout-gfs_post.log 2>&1
    ################################################################################
    # checkout_gtg
    ## yes: The gtg code at NCAR private repository is available for ops. GFS only.
    #       Only approved persons/groups have access permission.
    ## no:  No need to check out gtg code for general GFS users.
    ################################################################################
    checkout_gtg=${checkout_gtg:-"NO"}
    if [[ ${checkout_gtg} == "YES" ]] ; then
      cd gfs_post.fd
      cp sorc/post_gtg.fd/*f90 sorc/ncep_post.fd/.
      cp sorc/post_gtg.fd/gtg.config.gfs parm/gtg.config.gfs
    fi
    cd ${topdir}
else
    echo 'Skip.  Directory gfs_post.fd already exists.'
fi

checkout_wafs=${checkout_wafs:-"NO"}
if [[ ${checkout_wafs} == "YES" ]] ; then
  echo EMC_gfs_wafs checkout ...
  if [[ ! -d gfs_wafs.fd ]] ; then
    rm -f ${topdir}/checkout-gfs_wafs.log
    git clone --recursive --branch gfs_wafs.v6.3.3 https://github.com/NOAA-EMC/EMC_gfs_wafs.git gfs_wafs.fd >> ${topdir}/checkout-gfs_wafs.log 2>&1
    cd ${topdir}
  else
    echo 'Skip.  Directory gfs_wafs.fd already exists.'
  fi
fi

echo EMC_verif-global checkout ...
if [[ ! -d verif-global.fd ]] ; then
    rm -f ${topdir}/checkout-verif-global.log
    git clone --recursive --branch verif_global_v2.10.0 https://github.com/NOAA-EMC/EMC_verif-global.git verif-global.fd >> ${topdir}/checkout-verif-global.log 2>&1
    cd ${topdir}
else
    echo 'Skip. Directory verif-global.fd already exist.'
fi

exit 0
